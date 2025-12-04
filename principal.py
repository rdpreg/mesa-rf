import streamlit as st
import pandas as pd
import os
from datetime import datetime

HIST_FILE = "historico_rf.parquet"

st.set_page_config(
    page_title="Estoque de Renda Fixa",
    layout="wide"
)

st.title("Painel de Estoque de Renda Fixa")

st.sidebar.header("Configurações")

# AuC total da Convexa informado manualmente
auc_total_convexa = st.sidebar.number_input(
    "AuC total da Convexa (R$)",
    min_value=0.0,
    value=0.0,
    step=100000.0,
    format="%.2f",
    help="Digite o AuC total do escritório para cálculo das porcentagens."
)

uploaded_file = st.sidebar.file_uploader(
    "Envie o arquivo de Posição de Renda Fixa (CSV ou Excel)",
    type=["csv", "xlsx"]
)

salvar_historico = st.sidebar.checkbox(
    "Salvar este dia no histórico",
    value=False,
    help="Registra um resumo desta data em arquivo local para análise histórica."
)

if uploaded_file is None:
    st.info("Envie o arquivo de posição de renda fixa para iniciar a análise.")
    st.stop()

# ===================================================================
# Carregamento do arquivo
# ===================================================================

@st.cache_data
def load_data(file):
    name = file.name.lower()
    if name.endswith(".csv"):
        # Ajuste se o separador do seu arquivo for outro
        df = pd.read_csv(file, sep=";", decimal=",")
    else:
        df = pd.read_excel(file)
    return df

df_raw = load_data(uploaded_file)

st.subheader("Pré visualização dos dados")
st.dataframe(df_raw.head())

# ===================================================================
# Mapeamento de colunas
# ===================================================================

st.markdown("### Mapeamento de colunas")

colunas = list(df_raw.columns)

c1, c2, c3, c4 = st.columns(4)
with c1:
    col_data_ref = st.selectbox("Coluna de data de referência", colunas)
with c2:
    col_conta = st.selectbox("Coluna de conta", colunas)
with c3:
    col_cliente = st.selectbox("Coluna de cliente (opcional)", colunas)
with c4:
    col_ativo = st.selectbox("Coluna de nome do ativo", colunas)

c5, c6, c7 = st.columns(3)
with c5:
    col_tipo_produto = st.selectbox("Coluna de tipo de produto", colunas)
with c6:
    col_valor_bruto = st.selectbox("Coluna Valor Bruto - Curva Cliente", colunas)
with c7:
    col_valor_liquido = st.selectbox("Coluna Valor Líquido - Curva Cliente", colunas)

# Renomeia para padrão interno
df = df_raw.rename(columns={
    col_data_ref: "data_ref",
    col_conta: "conta",
    col_cliente: "cliente",
    col_ativo: "ativo",
    col_tipo_produto: "tipo_produto",
    col_valor_bruto: "valor_bruto",
    col_valor_liquido: "valor_liquido",
})

# Garante que as colunas de valor existam mesmo que não tenham sido mapeadas
if "valor_bruto" not in df.columns:
    df["valor_bruto"] = pd.NA

if "valor_liquido" not in df.columns:
    df["valor_liquido"] = pd.NA

# Conversões básicas
df["data_ref"] = pd.to_datetime(df["data_ref"], errors="coerce")
df["valor_bruto"] = pd.to_numeric(df["valor_bruto"], errors="coerce")
df["valor_liquido"] = pd.to_numeric(df["valor_liquido"], errors="coerce")


# Escolha do valor de RF usando a regra:
# - Se as duas colunas tiverem valor, usar o menor
# - Se só uma tiver valor, usar a que não estiver vazia
# - Se ambas vazias, considerar 0
def escolher_valor_rf(row):
    vb = row["valor_bruto"]
    vl = row["valor_liquido"]

    if pd.isna(vb) and pd.isna(vl):
        return 0.0
    if pd.isna(vb):
        return vl
    if pd.isna(vl):
        return vb
    return min(vb, vl)

df["valor_rf"] = df.apply(escolher_valor_rf, axis=1)

# Descobre data de referência principal
data_ref_unica = df["data_ref"].max()
st.write(f"**Data de referência da posição:** {data_ref_unica.date() if pd.notna(data_ref_unica) else 'não identificada'}")

# ===================================================================
# Classificação por classe de RF
# ===================================================================

st.markdown("### Classificação por classe de Renda Fixa")

BANCARIOS = {"CDB", "LCA", "LCI", "LC", "LIG", "LCD"}
CREDITO_PRIVADO = {"CRA", "CRI", "CDCA", "DEBENTURES", "DEBENTURE"}
TITULOS_PUBLICOS = {"LFT", "LTN", "NTNB", "NTNF", "NTNB-P", "NTNBP"}
BANCARIOS_EX_FGC = {"LF", "LFSN", "LFSC"}

def classificar_linha(tipo_produto, nome_ativo):
    tp = str(tipo_produto).upper().strip()
    nome = str(nome_ativo).upper()

    # Tesouro direto sempre pela descrição do ativo
    if "TESOURO DIRETO" in nome:
        return "Tesouro"

    if tp in BANCARIOS:
        return "Bancários"
    if tp in CREDITO_PRIVADO:
        return "Crédito Privado"
    if tp in TITULOS_PUBLICOS:
        return "Títulos Públicos"
    if tp in BANCARIOS_EX_FGC:
        return "Bancários ex-FGC"

    return None  # fica para classificação manual

df["classe_auto"] = df.apply(
    lambda row: classificar_linha(row["tipo_produto"], row["ativo"]),
    axis=1
)

# Produtos ainda sem classe
produtos_sem_classe = sorted(
    df.loc[df["classe_auto"].isna(), "tipo_produto"].dropna().unique()
)

class_map_manual = {}
if len(produtos_sem_classe) > 0:
    st.warning("Foram encontrados produtos sem classificação de classe. Escolha a classe para cada um.")
    opcoes_classe = [
        "Bancários",
        "Crédito Privado",
        "Títulos Públicos",
        "Tesouro",
        "Bancários ex-FGC",
        "Ignorar"
    ]
    for p in produtos_sem_classe:
        escolha = st.selectbox(
            f"Classe para o produto: {p}",
            opcoes_classe,
            index=0,
            key=f"class_{p}"
        )
        class_map_manual[p] = escolha

def definir_classe_final(row):
    if pd.notna(row["classe_auto"]):
        return row["classe_auto"]
    tp = row["tipo_produto"]
    classe_manual = class_map_manual.get(tp)
    if classe_manual is None:
        return "Outros"
    if classe_manual == "Ignorar":
        return "Outros"
    return classe_manual

df["classe_rf"] = df.apply(definir_classe_final, axis=1)

# ===================================================================
# Cálculos principais
# ===================================================================

total_rf = df["valor_rf"].sum()
contas_com_rf = df[df["valor_rf"] > 0]["conta"].nunique()

if auc_total_convexa <= 0:
    st.error("Informe o AuC total da Convexa na barra lateral para cálculo das porcentagens.")
    st.stop()

pct_rf_sobre_auc = (total_rf / auc_total_convexa * 100) if auc_total_convexa > 0 else 0.0

st.markdown("## Visão geral")

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Contas com ativos de Renda Fixa", f"{contas_com_rf}")
with m2:
    st.metric(
        "AuC total em Renda Fixa",
        f"R$ {total_rf:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )
with m3:
    st.metric(
        "% RF sobre AuC Convexa",
        f"{pct_rf_sobre_auc:.2f}%"
    )

st.divider()

aba_prod, aba_classe, aba_hist = st.tabs(["Por produto", "Por classe", "Histórico"])

# ===================================================================
# Aba: Por produto
# ===================================================================

with aba_prod:
    st.subheader("AuC de Renda Fixa por produto")

    grp_prod = (
        df.groupby("tipo_produto", dropna=False)["valor_rf"]
        .sum()
        .reset_index()
        .rename(columns={"valor_rf": "auc_rf"})
    )

    grp_prod["pct_estoque_rf"] = grp_prod["auc_rf"] / total_rf * 100 if total_rf > 0 else 0.0
    grp_prod["pct_auc_convexa"] = grp_prod["auc_rf"] / auc_total_convexa * 100 if auc_total_convexa > 0 else 0.0

    grp_prod_sorted = grp_prod.sort_values("auc_rf", ascending=False)

    st.dataframe(
        grp_prod_sorted.style.format({
            "auc_rf": "R$ {:,.2f}".format,
            "pct_estoque_rf": "{:.2f}%",
            "pct_auc_convexa": "{:.2f}%"
        })
    )

    st.bar_chart(
        grp_prod_sorted.set_index("tipo_produto")["auc_rf"],
        use_container_width=True
    )

# ===================================================================
# Aba: Por classe
# ===================================================================

with aba_classe:
    st.subheader("AuC de Renda Fixa por classe")

    grp_class = (
        df.groupby("classe_rf", dropna=False)["valor_rf"]
        .sum()
        .reset_index()
        .rename(columns={"valor_rf": "auc_rf"})
    )

    grp_class["pct_estoque_rf"] = grp_class["auc_rf"] / total_rf * 100 if total_rf > 0 else 0.0
    grp_class["pct_auc_convexa"] = grp_class["auc_rf"] / auc_total_convexa * 100 if auc_total_convexa > 0 else 0.0

    grp_class_sorted = grp_class.sort_values("auc_rf", ascending=False)

    st.dataframe(
        grp_class_sorted.style.format({
            "auc_rf": "R$ {:,.2f}".format,
            "pct_estoque_rf": "{:.2f}%",
            "pct_auc_convexa": "{:.2f}%"
        })
    )

    st.bar_chart(
        grp_class_sorted.set_index("classe_rf")["auc_rf"],
        use_container_width=True
    )

# ===================================================================
# Aba: Histórico
# ===================================================================

with aba_hist:
    st.subheader("Histórico mês a mês")

    # Monta resumo do dia para histórico
    if pd.notna(data_ref_unica):
        resumo_total = pd.DataFrame([{
            "data_ref": data_ref_unica,
            "tipo": "total",
            "categoria": "RF Total",
            "auc_rf": total_rf,
            "auc_total_convexa": auc_total_convexa
        }])

        resumo_prod = grp_prod.copy()
        resumo_prod["data_ref"] = data_ref_unica
        resumo_prod["tipo"] = "produto"
        resumo_prod = resumo_prod.rename(columns={"tipo_produto": "categoria"})
        resumo_prod["auc_total_convexa"] = auc_total_convexa
        resumo_prod = resumo_prod[["data_ref", "tipo", "categoria", "auc_rf", "auc_total_convexa"]]

        resumo_class = grp_class.copy()
        resumo_class["data_ref"] = data_ref_unica
        resumo_class["tipo"] = "classe"
        resumo_class = resumo_class.rename(columns={"classe_rf": "categoria"})
        resumo_class["auc_total_convexa"] = auc_total_convexa
        resumo_class = resumo_class[["data_ref", "tipo", "categoria", "auc_rf", "auc_total_convexa"]]

        resumo_dia = pd.concat([resumo_total, resumo_prod, resumo_class], ignore_index=True)
    else:
        resumo_dia = None

    # Salva histórico se marcado
    if salvar_historico and resumo_dia is not None:
        if os.path.exists(HIST_FILE):
            hist_antigo = pd.read_parquet(HIST_FILE)
            hist_novo = pd.concat([hist_antigo, resumo_dia], ignore_index=True)
        else:
            hist_novo = resumo_dia

        hist_novo.to_parquet(HIST_FILE, index=False)
        st.success("Histórico atualizado com a posição atual.")

    # Carrega histórico para visualização
    if os.path.exists(HIST_FILE):
        historico = pd.read_parquet(HIST_FILE)
        historico["data_ref"] = pd.to_datetime(historico["data_ref"])
        historico["mes"] = historico["data_ref"].dt.to_period("M").dt.to_timestamp()

        st.markdown("#### Estoque total de RF por mês")

        hist_total = historico[historico["tipo"] == "total"].groupby("mes")["auc_rf"].sum().reset_index()
        hist_total = hist_total.set_index("mes")

        st.line_chart(hist_total, use_container_width=True)

        st.markdown("#### Histórico por produto")

        hist_prod = historico[historico["tipo"] == "produto"].groupby(["mes", "categoria"])["auc_rf"].sum().reset_index()
        if not hist_prod.empty:
            pivot_prod = hist_prod.pivot(index="mes", columns="categoria", values="auc_rf").fillna(0.0)
            st.line_chart(pivot_prod, use_container_width=True)
        else:
            st.info("Ainda não há dados históricos por produto.")

        st.markdown("#### Histórico por classe")

        hist_class = historico[historico["tipo"] == "classe"].groupby(["mes", "categoria"])["auc_rf"].sum().reset_index()
        if not hist_class.empty:
            pivot_class = hist_class.pivot(index="mes", columns="categoria", values="auc_rf").fillna(0.0)
            st.line_chart(pivot_class, use_container_width=True)
        else:
            st.info("Ainda não há dados históricos por classe.")
    else:
        st.info("Ainda não há histórico salvo. Marque a opção de salvar histórico na barra lateral para começar a construir a série.")
