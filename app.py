# -*- coding: utf-8 -*-
"""
Streamlit • MVP de Valoração de Patentes (Wizard 4 passos, 1 projeto)
--------------------------------------------------------------------
- Single-file app:  streamlit run app.py
- Dependências mínimas:  streamlit, pandas, numpy, xlsxwriter (para Excel)
- Persistência: exporta/importa **um** arquivo .json com tudo da sessão (um projeto)

ATENÇÃO (MVP):
- Sem "Projetos[]" nem "Status" (Rascunho/Concluído) — removidos conforme pedido.
- Sem "Faixa recomendada (85%/115%)" — removida do escopo.
- Inclui: Wizard 4 passos, blocos qualitativos, premissas quantitativas, DCF (3 cenários), Abordagem de Custos (soma simples), validações, tabela e gráfico comparativo, exportações JSON/CSV/Excel.
"""

from __future__ import annotations
import json
from dataclasses import asdict, dataclass, field
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import streamlit as st


PRIMARY_COLOR = "#16a34a"
PRIMARY_COLOR_DARK = "#15803d"

# =============================================================
# Utilidades numéricas
# =============================================================

def money(x: Optional[float], precision: int = 2) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return f"R$ {x:,.{precision}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def npv(cash_flows: List[float], discount_rate: float) -> float:
    r = discount_rate
    return float(sum(cf / ((1 + r) ** t) for t, cf in enumerate(cash_flows, start=1)))


# =============================================================
# Data model — 1 projeto
# =============================================================

@dataclass
class Qualitativo:
    sumario_executivo: str = ""
    descricao_tecnologia: str = ""
    analise_mercado: str = ""
    analise_competitiva: str = ""  # opcional
    analise_riscos: str = ""        # opcional


@dataclass
class Premissas:
    nome_projeto: str = "Avaliação #1"
    descricao: str = ""
    # Dados Financeiros
    volume_negocios_anual: float = 1_000_000.0
    custos_variaveis_percentual: float = 0.30
    custos_fixos: float = 300_000.0
    taxa_royalties: float = 0.05           # 0..0.20
    # Dados de Mercado
    taxa_crescimento: float = 0.05         # 5% a.a.
    variacao_receita_otimista: float = 0.20
    variacao_receita_pessimista: float = 0.20
    taxa_desconto: float = 0.12            # 12% a.a.
    # Horizontes
    horizonte_proj_anos: int = 10          # 5..20
    # Custos de Desenvolvimento (abordagem de custos)
    custos_pd: float = 300_000.0
    custos_formulacao: float = 120_000.0
    custos_testes: float = 150_000.0
    custos_prototipo: float = 200_000.0
    custos_validacao: float = 80_000.0


@dataclass
class ResultadoMetodo:
    valor: Optional[float] = None
    detalhes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Resultados:
    dcf_prob: ResultadoMetodo = field(default_factory=ResultadoMetodo)
    dcf_otim: ResultadoMetodo = field(default_factory=ResultadoMetodo)
    dcf_pess: ResultadoMetodo = field(default_factory=ResultadoMetodo)
    custos: ResultadoMetodo = field(default_factory=ResultadoMetodo)


@dataclass
class Projeto:
    qualitativo: Qualitativo = field(default_factory=Qualitativo)
    premissas: Premissas = field(default_factory=Premissas)
    resultados: Resultados = field(default_factory=Resultados)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(s: str) -> "Projeto":
        data = json.loads(s)
        return Projeto(
            qualitativo=Qualitativo(**data.get("qualitativo", {})),
            premissas=Premissas(**data.get("premissas", {})),
            resultados=Resultados(
                dcf_prob=ResultadoMetodo(**data.get("resultados", {}).get("dcf_prob", {})),
                dcf_otim=ResultadoMetodo(**data.get("resultados", {}).get("dcf_otim", {})),
                dcf_pess=ResultadoMetodo(**data.get("resultados", {}).get("dcf_pess", {})),
                custos=ResultadoMetodo(**data.get("resultados", {}).get("custos", {})),
            ),
        )


if "projeto" not in st.session_state:
    st.session_state.projeto = Projeto()

P: Projeto = st.session_state.projeto

# =============================================================
# Cálculos
# =============================================================

def validar_premissas(p: Premissas) -> List[str]:
    errs: List[str] = []
    if not p.nome_projeto.strip():
        errs.append("Nome do Projeto é obrigatório.")
    if p.volume_negocios_anual < 0:
        errs.append("Volume de Negócios Anual não pode ser negativo.")
    if not (0.0 <= p.custos_variaveis_percentual <= 1.0):
        errs.append("Custos e Despesas Variáveis devem estar entre 0% e 100% da receita.")
    if p.custos_fixos < 0:
        errs.append("Custos e Despesas Fixos não podem ser negativos.")
    if not (0.0 <= p.taxa_royalties <= 0.20):
        errs.append("Taxa de Royalties deve estar entre 0% e 20%.")
    if p.variacao_receita_otimista < 0:
        errs.append("A variação de receita do cenário otimista deve ser positiva.")
    if p.variacao_receita_pessimista < 0:
        errs.append("A variação de receita do cenário pessimista deve ser positiva.")
    if p.taxa_desconto <= 0:
        errs.append("Taxa de Desconto deve ser positiva.")
    if p.horizonte_proj_anos < 1:
        errs.append("Horizonte de Projeção deve ser ≥ 1 ano.")
    for c in [p.custos_pd, p.custos_formulacao, p.custos_testes, p.custos_prototipo, p.custos_validacao]:
        if c < 0:
            errs.append("Custos de desenvolvimento não podem ser negativos.")
            break
    return errs


def projetar_fluxo_caixa(p: Premissas, g: float, ajuste_receita: float = 0.0) -> pd.DataFrame:
    receita0_ajustada = p.volume_negocios_anual * (1 + ajuste_receita)
    linhas: List[Dict[str, float | int]] = []
    for t in range(1, p.horizonte_proj_anos + 1):
        receita_t = receita0_ajustada * ((1 + g) ** t)
        custos_variaveis_t = receita_t * p.custos_variaveis_percentual
        lucro_t = receita_t - custos_variaveis_t - p.custos_fixos
        royalties_t = p.taxa_royalties * max(lucro_t, 0.0)
        fator_desconto_t = (1 + p.taxa_desconto) ** t
        vp_royalties_t = royalties_t / fator_desconto_t
        linhas.append(
            {
                "Ano": t,
                "Receita": float(receita_t),
                "Custos/Despesas Variáveis": float(custos_variaveis_t),
                "Custos/Despesas Fixos": float(p.custos_fixos),
                "Lucro": float(lucro_t),
                "Royalties": float(royalties_t),
                "Fator de desconto": float(fator_desconto_t),
                "Royalties descontados": float(vp_royalties_t),
            }
        )
    return pd.DataFrame(linhas)


def _fluxos_fcff(p: Premissas, g: float, ajuste_receita: float = 0.0) -> List[float]:
    projecao = projetar_fluxo_caixa(p, g, ajuste_receita)
    return projecao["Royalties"].astype(float).tolist()


def calcular_dcf_cenario(p: Premissas, g: float, nome: str, ajuste_receita: float = 0.0) -> ResultadoMetodo:
    projecao = projetar_fluxo_caixa(p, g, ajuste_receita)
    fluxos = projecao["Royalties"].astype(float).tolist()
    valor = npv(fluxos, p.taxa_desconto)
    return ResultadoMetodo(
        valor=float(valor),
        detalhes={
            "cenario": nome,
            "g": g,
            "ajuste_receita": ajuste_receita,
            "fluxos": fluxos,
            "projecao_caixa": projecao.to_dict(orient="records"),
        },
    )


def calcular_custos(p: Premissas) -> ResultadoMetodo:
    total = p.custos_pd + p.custos_formulacao + p.custos_testes + p.custos_prototipo + p.custos_validacao
    return ResultadoMetodo(valor=float(total), detalhes={"soma_custos": float(total)})


# =============================================================
# UI — Wizard 4 passos
# =============================================================

st.set_page_config(page_title="Valoração de Patentes — MVP", layout="wide")

st.markdown(
    f"""
    <style>
    .stButton > button,
    .stDownloadButton > button {{
        background-color: {PRIMARY_COLOR} !important;
        border-color: {PRIMARY_COLOR} !important;
        color: white !important;
    }}
    .stButton > button:hover,
    .stDownloadButton > button:hover {{
        background-color: {PRIMARY_COLOR_DARK} !important;
        border-color: {PRIMARY_COLOR_DARK} !important;
        color: white !important;
    }}
    .stButton > button:focus:not(:active),
    .stDownloadButton > button:focus:not(:active) {{
        box-shadow: 0 0 0 0.2rem rgba(22, 163, 74, 0.35) !important;
        color: white !important;
    }}
    .stProgress > div > div > div > div {{
        background-color: {PRIMARY_COLOR} !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("💡 Valoração de Patentes — MVP (Wizard)")

if "step" not in st.session_state:
    st.session_state.step = 1

with st.sidebar:
    current_step = st.session_state.step
    st.header("Navegação")
    st.write("Passos: 1️⃣ Textos • 2️⃣ Premissas • 3️⃣ Cálculo • 4️⃣ Relatório")
    progress_placeholder = st.empty()
    st.markdown("### Ir diretamente para")
    steps_labels = [
        (1, "Passo 1 — Textos"),
        (2, "Passo 2 — Premissas"),
        (3, "Passo 3 — Cálculo"),
        (4, "Passo 4 — Relatório"),
    ]
    for idx, label in steps_labels:
        if st.button(label, key=f"sidebar_step_{idx}"):
            st.session_state.step = idx
            current_step = idx
    progress_placeholder.progress((current_step - 1) / 3)

step = st.session_state.step

# -----------------------------
# Passo 1 — Informações Textuais
# -----------------------------
if step == 1:
    st.subheader("Passo 1 — Informações Textuais")

    P.premissas.nome_projeto = st.text_input("Nome do Projeto", P.premissas.nome_projeto)
    P.premissas.descricao = st.text_area("Descrição (resumo do que será valorado)", P.premissas.descricao, height=80)

    st.markdown("**Sumário Executivo**")
    P.qualitativo.sumario_executivo = st.text_area("Descreva brevemente a tecnologia, problema e benefícios.", P.qualitativo.sumario_executivo, height=120)

    st.markdown("**Descrição da Tecnologia**")
    P.qualitativo.descricao_tecnologia = st.text_area("Como funciona? Aspectos técnicos e diferenciais.", P.qualitativo.descricao_tecnologia, height=160)

    st.markdown("**Análise de Mercado**")
    P.qualitativo.analise_mercado = st.text_area("Mercado-alvo, tamanho, tendências e oportunidades.", P.qualitativo.analise_mercado, height=140)

    colA, colB = st.columns(2)
    with colA:
        st.markdown("**Análise Competitiva (opcional)**")
        P.qualitativo.analise_competitiva = st.text_area("Concorrentes, vantagens e barreiras.", P.qualitativo.analise_competitiva, height=120)
    with colB:
        st.markdown("**Análise de Riscos (opcional)**")
        P.qualitativo.analise_riscos = st.text_area("Riscos técnicos, de mercado e regulatórios.", P.qualitativo.analise_riscos, height=120)

    col1, col2 = st.columns([1,1])
    if col1.button("➡️ Avançar para Premissas", type="primary"):
        st.session_state.step = 2
    col2.download_button("⬇️ Exportar projeto (.json)", data=P.to_json(), file_name=f"{P.premissas.nome_projeto.replace(' ', '_')}.patval.json", mime="application/json")

# -----------------------------
# Passo 2 — Premissas Quantitativas
# -----------------------------
elif step == 2:
    st.subheader("Passo 2 — Premissas Quantitativas")

    with st.expander("Dados Financeiros", expanded=True):
        c1, c2, c3 = st.columns(3)
        P.premissas.volume_negocios_anual = c1.number_input(
            "Volume de Negócios Anual (R$)",
            0.0,
            1e12,
            P.premissas.volume_negocios_anual,
            step=10_000.0,
            help="Receita anual esperada no ano base da projeção.",
        )
        P.premissas.custos_variaveis_percentual = c2.number_input(
            "Custos e Despesas Variáveis (% da Receita)",
            0.0,
            100.0,
            P.premissas.custos_variaveis_percentual * 100,
            step=0.5,
            help="Inclua itens que variam com o faturamento, como comissões, impostos sobre vendas e insumos variáveis.",
        ) / 100
        P.premissas.custos_fixos = c3.number_input(
            "Custos e Despesas Fixos (R$)",
            0.0,
            1e12,
            P.premissas.custos_fixos,
            step=10_000.0,
            help="Inclua despesas que não dependem diretamente da receita, como equipe fixa, aluguel e estrutura administrativa.",
        )

        c4, c5 = st.columns(2)
        P.premissas.taxa_royalties = c4.number_input(
            "Taxa de Royalties sobre o Lucro (%)",
            0.0,
            20.0,
            P.premissas.taxa_royalties * 100,
            step=0.25,
            help="Esta taxa incide sobre o lucro do período: Receita - Custos/Despesas Variáveis - Custos/Despesas Fixos.",
        ) / 100
        P.premissas.horizonte_proj_anos = int(c5.number_input(
            "Horizonte de Projeção (anos)",
            1,
            40,
            P.premissas.horizonte_proj_anos,
            help="Defina com base na vida estimada de exploração comercial da patente.",
        ))

    with st.expander("Dados de Mercado", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        P.premissas.taxa_crescimento = c1.number_input(
            "Taxa de Crescimento (% a.a.)",
            -50.0,
            100.0,
            P.premissas.taxa_crescimento * 100,
            step=0.5,
            help="Crescimento orgânico anual da receita no cenário provável.",
        ) / 100
        P.premissas.variacao_receita_otimista = c2.number_input(
            "Aumento de Receita no cenário otimista (%)",
            0.0,
            300.0,
            P.premissas.variacao_receita_otimista * 100,
            step=1.0,
            help="Percentual adicional sobre a receita projetada para construir o cenário otimista.",
        ) / 100
        P.premissas.variacao_receita_pessimista = c3.number_input(
            "Redução de Receita no cenário pessimista (%)",
            0.0,
            100.0,
            P.premissas.variacao_receita_pessimista * 100,
            step=1.0,
            help="Percentual de redução sobre a receita projetada para construir o cenário pessimista.",
        ) / 100
        P.premissas.taxa_desconto = c4.number_input(
            "Taxa de Desconto (% a.a.)",
            0.01,
            100.0,
            P.premissas.taxa_desconto * 100,
            step=0.5,
            help="Taxa usada para trazer os fluxos de royalties a valor presente.",
        ) / 100

    with st.expander("Custos de Desenvolvimento (Abordagem de Custos)", expanded=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        P.premissas.custos_pd = c1.number_input("P&D (R$)", 0.0, 1e12, P.premissas.custos_pd, step=10_000.0)
        P.premissas.custos_formulacao = c2.number_input("Formulação (R$)", 0.0, 1e12, P.premissas.custos_formulacao, step=5_000.0)
        P.premissas.custos_testes = c3.number_input("Testes (R$)", 0.0, 1e12, P.premissas.custos_testes, step=5_000.0)
        P.premissas.custos_prototipo = c4.number_input("Protótipo (R$)", 0.0, 1e12, P.premissas.custos_prototipo, step=5_000.0)
        P.premissas.custos_validacao = c5.number_input("Validação (R$)", 0.0, 1e12, P.premissas.custos_validacao, step=5_000.0)

    col1, col2 = st.columns([1,1])
    if col1.button("⬅️ Voltar para Textos"):
        st.session_state.step = 1
    if col2.button("➡️ Avançar para Cálculo", type="primary"):
        erros = validar_premissas(P.premissas)
        if erros:
            for e in erros:
                st.error(e)
        else:
            st.session_state.step = 3

# -----------------------------
# Passo 3 — Cálculos Automáticos
# -----------------------------
elif step == 3:
    st.subheader("Passo 3 — Cálculos Automáticos")
    st.caption(
        "Como o FDC/DCF é calculado: projetamos a receita por ano, deduzimos custos/despesas variáveis e fixos, "
        "calculamos o lucro, aplicamos a taxa de royalties sobre esse lucro (piso em zero) e descontamos cada fluxo "
        "pela taxa de desconto informada."
    )

    # Validar novamente (defensivo)
    erros = validar_premissas(P.premissas)
    if erros:
        st.warning("Ajuste as premissas no Passo 2 antes de calcular.")
        for e in erros:
            st.error(e)
    else:
        # Executar cálculos
        g_prob = P.premissas.taxa_crescimento
        g_otim = P.premissas.taxa_crescimento
        g_pess = P.premissas.taxa_crescimento
        P.resultados.dcf_prob = calcular_dcf_cenario(P.premissas, g_prob, "Provável")
        P.resultados.dcf_otim = calcular_dcf_cenario(P.premissas, g_otim, "Otimista", ajuste_receita=P.premissas.variacao_receita_otimista)
        P.resultados.dcf_pess = calcular_dcf_cenario(P.premissas, g_pess, "Pessimista", ajuste_receita=-P.premissas.variacao_receita_pessimista)
        P.resultados.custos = calcular_custos(P.premissas)

        valores = {
            "DCF (Provável)": P.resultados.dcf_prob.valor,
            "DCF (Otimista)": P.resultados.dcf_otim.valor,
            "DCF (Pessimista)": P.resultados.dcf_pess.valor,
            "Custos (soma)": P.resultados.custos.valor,
        }
        df_comp = pd.DataFrame({"Método": list(valores.keys()), "Valor": list(valores.values())})

        colA, colB = st.columns([2,1])
        with colA:
            st.dataframe(df_comp, use_container_width=True)
        with colB:
            if not df_comp["Valor"].isna().all():
                # Ensure values are a numpy ndarray of float for type-checkers and numpy functions
                vals = pd.to_numeric(df_comp["Valor"], errors="coerce").to_numpy(dtype=float)
                st.metric("Mediana", money(float(np.nanmedian(vals))))
                st.metric("Média", money(float(np.nanmean(vals))))

        st.bar_chart(df_comp.set_index("Método"))

        st.markdown("#### Projeção do fluxo de caixa (royalties)")
        tabs = st.tabs(["Provável", "Otimista", "Pessimista"])
        for tab, res in zip(tabs, [P.resultados.dcf_prob, P.resultados.dcf_otim, P.resultados.dcf_pess]):
            with tab:
                proj = pd.DataFrame(res.detalhes.get("projecao_caixa", []))
                if not proj.empty:
                    st.dataframe(proj, use_container_width=True)
                    st.line_chart(proj.set_index("Ano")[["Royalties", "Royalties descontados"]])
                else:
                    st.info("Sem projeção disponível para este cenário.")

        csv = df_comp.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Exportar resultados (.csv)", data=csv, file_name="resultados_valoracao.csv", mime="text/csv")

    col1, col2 = st.columns([1,1])
    if col1.button("⬅️ Voltar para Premissas"):
        st.session_state.step = 2
    if col2.button("➡️ Avançar para Relatório", type="primary"):
        st.session_state.step = 4

# -----------------------------
# Passo 4 — Relatório Final (visualização + exportações)
# -----------------------------
elif step == 4:
    st.subheader("Passo 4 — Relatório Final")

    st.markdown(f"### {P.premissas.nome_projeto}")
    st.write(P.premissas.descricao)

    st.markdown("#### Blocos Qualitativos")

    def _render_section(label: str, content: Optional[str], show_when_empty: bool = True) -> None:
        texto = (content or "").strip()
        if texto or show_when_empty:
            st.markdown(f"**{label}**\n\n{texto if texto else '—'}")

    _render_section("Sumário Executivo", P.qualitativo.sumario_executivo)
    _render_section("Descrição da Tecnologia", P.qualitativo.descricao_tecnologia)
    _render_section("Análise de Mercado", P.qualitativo.analise_mercado)
    _render_section("Análise Competitiva", P.qualitativo.analise_competitiva, show_when_empty=False)
    _render_section("Análise de Riscos", P.qualitativo.analise_riscos, show_when_empty=False)

    st.markdown("#### Premissas principais")
    prem_df = pd.DataFrame([
        ["Volume de Negócios (ano 1)", money(P.premissas.volume_negocios_anual)],
        ["Custos/Despesas Variáveis", f"{P.premissas.custos_variaveis_percentual*100:.2f}% da receita"],
        ["Custos/Despesas Fixos", money(P.premissas.custos_fixos)],
        ["Taxa de Royalties sobre Lucro", f"{P.premissas.taxa_royalties*100:.2f}%"],
        ["Taxa de Crescimento (g)", f"{P.premissas.taxa_crescimento*100:.2f}%"],
        ["Ajuste otimista de receita", f"+{P.premissas.variacao_receita_otimista*100:.2f}%"],
        ["Ajuste pessimista de receita", f"-{P.premissas.variacao_receita_pessimista*100:.2f}%"],
        ["Taxa de Desconto (r)", f"{P.premissas.taxa_desconto*100:.2f}%"],
        ["Horizonte (anos)", P.premissas.horizonte_proj_anos],
        ["Custos de Desenvolvimento (soma)", money(P.premissas.custos_pd + P.premissas.custos_formulacao + P.premissas.custos_testes + P.premissas.custos_prototipo + P.premissas.custos_validacao)],
    ], columns=["Item", "Valor"])
    st.table(prem_df)

    # Tabela resumo de valores
    valores = {
        "DCF (Provável)": P.resultados.dcf_prob.valor,
        "DCF (Otimista)": P.resultados.dcf_otim.valor,
        "DCF (Pessimista)": P.resultados.dcf_pess.valor,
        "Custos (soma)": P.resultados.custos.valor,
    }
    df_comp = pd.DataFrame({"Método": list(valores.keys()), "Valor": list(valores.values())})
    st.dataframe(df_comp, use_container_width=True)

    with st.expander("Ver projeção do fluxo de caixa por cenário", expanded=False):
        tabs_rel = st.tabs(["Provável", "Otimista", "Pessimista"])
        for tab, res in zip(tabs_rel, [P.resultados.dcf_prob, P.resultados.dcf_otim, P.resultados.dcf_pess]):
            with tab:
                proj = pd.DataFrame(res.detalhes.get("projecao_caixa", []))
                if not proj.empty:
                    st.dataframe(proj, use_container_width=True)

    # Exportações
    col1, col2, col3 = st.columns(3)
    col1.download_button("⬇️ Exportar projeto (.json)", data=P.to_json(), file_name=f"{P.premissas.nome_projeto.replace(' ', '_')}.patval.json", mime="application/json")

    csv = df_comp.to_csv(index=False).encode("utf-8")
    col2.download_button("⬇️ Exportar resultados (.csv)", data=csv, file_name="resultados_valoracao.csv", mime="text/csv")

    # Excel detalhado (resumo + fluxos se disponíveis)
    try:
        import io
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df_comp.to_excel(writer, sheet_name="Resumo", index=False)
            # Fluxos (se existirem)
            for label, res in [
                ("DCF_Provavel_Fluxos", P.resultados.dcf_prob),
                ("DCF_Otimista_Fluxos", P.resultados.dcf_otim),
                ("DCF_Pessimista_Fluxos", P.resultados.dcf_pess),
            ]:
                if res and res.detalhes.get("fluxos"):
                    pd.DataFrame({"Ano": list(range(1, len(res.detalhes["fluxos"]) + 1)), "Fluxo": res.detalhes["fluxos"]}).to_excel(writer, sheet_name=label, index=False)
                proj = pd.DataFrame(res.detalhes.get("projecao_caixa", [])) if res else pd.DataFrame()
                if not proj.empty:
                    proj.to_excel(writer, sheet_name=f"{label}_Detalhe"[:31], index=False)
            # Premissas
            prem_export = pd.DataFrame(list(asdict(P.premissas).items()), columns=["Chave", "Valor"])
            prem_export.to_excel(writer, sheet_name="Premissas", index=False)
        col3.download_button("⬇️ Exportar Excel (.xlsx)", data=output.getvalue(), file_name="relatorio_valoracao.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        st.info("Instale 'xlsxwriter' para exportar Excel (pip install xlsxwriter).")

    col_back, col_reset = st.columns([1,1])
    if col_back.button("⬅️ Voltar para Cálculo"):
        st.session_state.step = 3
    if col_reset.button("🗑️ Limpar resultados (manter textos/premissas)"):
        P.resultados = Resultados()
        st.success("Resultados limpos.")
