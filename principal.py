import streamlit as st
import pandas as pd
import os
from datetime import datetime, date

HIST_FILE = "historico_rf.parquet"

st.set_page_config(
    page_title="Estoque de Renda Fixa",
    layout="wide"
)

st.title("Painel de Estoque de Renda Fixa")

st.sidebar.header("Configurações")

# Data de referência da posição
data_ref_input = st.sidebar.date_input(
    "Data de referência da posição",
    value=date.today()
)

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


@st.cache_data
def load_data(file):
    name = file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(file, sep=";", decimal=",")
    else:
        df = pd.read_excel(file, engine="openpyxl")
    return df


df_raw = load_data(uploaded_file)

st.subheader("Pré visualização dos dados")
st.dataframe(df_raw.head())

# ============================================================
# Validação e renomeação de colunas padrão BTG
# ============================================================

required_base = ["Conta", "Produto", "Ativo"]
# Agora trabalhamos com os dois brutos
value_cols = ["Valor Bruto - Curva Cliente", "Valor Bruto - Curva Mercado"]

missing_base = [c for c in required_base if c not in df_raw.columns]
if missing_base:
    st.error("As seguintes colunas obrigatórias não foram encontradas no arquivo:")
    st.write(missing_base)
    st.write("Colunas disponíveis:", list(df_raw.columns))
    st.stop()

if all(c not in df_raw.columns for c in value_cols):
    st.error("Não encontrei nenhuma coluna de valor bruto pela nomenclatura padrão.")
    st.write("Esperadas:", value_cols)
    st.write("Colunas disponíveis:", list(df_raw.columns))
    st.stop()

rename_dict = {
    "Conta": "conta",
    "Nome": "cliente",
    "Emissor": "emissor",
    "Produto": "tipo_produto",
    "Ativo": "ativo",
    "Valor Bruto - Curva Cliente": "valor_bruto_cliente",
    "Valor Bruto - Curva Mercado": "valor_bruto_mercado",
    # Mantemos o líquido mapeado caso queira usar depois, mas ele não entra no cálculo
    "Valor Líquido - Curva Cliente": "valor_liquido_cliente",
}

df = df_raw.rename(columns={k: v for k, v in rename_dict.items() if k in df_raw.columns})

if "cliente" not in df.columns:
    df["cliente"] = ""

if "valor_bruto_cliente" not in df.columns:
    df["valor_bruto_cliente"] = pd.NA
if "valor_bruto_mercado" not in df.columns:
    df["valor_bruto_mercado"] = pd.NA
if "valor_liquido_cliente" not in df.columns:
    df["valor_liquido_cliente"] = pd.NA

df["valor_bruto_cliente"] = pd.to_numeric(df["valor_bruto_cliente"], errors="coerce")
df["valor_bruto_mercado"] = pd.to_numeric(df["valor_bruto_mercado"], errors="coerce")
df["valor_liquido_cliente"] = pd.to_numeric(df["valor_liquido_cliente"], errors="coerce")

# Data ref igual para todas as linhas
df["data_ref"] = pd.to_datetime(data_ref_input)

# ============================================================
# Regra de escolha do valor de RF
# Sempre valor bruto, escolhendo entre curva cliente e curva mercado
# ============================================================

def escolher_valor_rf(row):
    vb_cli = row["valor_bruto_cliente"]
    vb_mer = row["valor_bruto_mercado"]

    if pd.isna(vb_cli) and pd.isna(vb_mer):
        return 0.0
    if pd.isna(vb_cli):
        return vb_mer
    if pd.isna(vb_mer):
        return vb_cli
    return min(vb_cli, vb_mer)


df["valor_rf"] = df.apply(escolher_valor_rf, axis=1)

data_ref_unica = df["data_ref"].max()
st.write(f"**Data de referência da posição:** {data_ref_unica.date()}")

# ============================================================
# Classificação por classe usando a lista final de produtos
# ============================================================

TESOURO_PREFIX = "TESOURO DIRETO"

BANCARIOS = {
    "CDB", "LCA", "LCI", "LC", "LIG", "LCD"
}

CREDITO_PRIVADO = {
    "CRA", "CRI", "CDCA", "DEBENTURE", "DEBÊNTURE"
}

TPF = {
    "NTNB", "LFT", "NTNF", "NTNB-P", "LTN"
}

BANCARIOS_EX_FGC = {
    "LF", "LFSN", "LFSC"
}

def classificar_linha(tipo_produto, nome_ativo):
    tp = str(tipo_produto).upper().strip()
    nome = str(nome_ativo).upper().strip()

    # Tesouro Direto
    if TESOURO_PREFIX in tp or TESOURO_PREFIX in nome:
        return "Tesouro"

    if tp in BANCARIOS:
        return "Bancário"
    if tp in CREDITO_PRIVADO:
        return "Crédito Privado"
    if tp in TPF:
        return "TPF"
    if tp in BANCARIOS_EX_FGC:
        return "Bancário ex-FGC"

    return "Outros"


df["classe_rf"] = df.apply(
    lambda row: classificar_linha(row["tipo_produto"], row["ativo"]),
    axis=1
)

# ============================================================
# Cálculos principais
# ============================================================

total_rf = df["valor_rf"].sum()
contas_com_rf = df[df["valor_rf"] > 0]["conta"].nunique()

if auc_total_convexa <= 0:
    st.error("Informe o AuC total da Convexa na barra lateral para cálculo das porcentagens.")
    st.stop()

pct_rf_sobre_auc = (total_rf / auc_total_convexa * 100) if auc_total_convexa > 0 else 0.0

st.markdown("## Visão geral")

def formata_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Contas com ativos de Renda Fixa", f"{contas_com_rf}")
with m2:
    st.metric("AuC total em Renda Fixa", formata_moeda(total_rf))
with m3:
    st.metric("% RF sobre AuC Convexa", f"{pct_rf_sobre_auc:.2f}%")

st.divider()

# Agora com a aba Por emissor
aba_prod, aba_classe, aba_emissor, aba_hist = st.tabs(
    ["Por produto", "Por classe", "Por emissor", "Histórico"]
)

# ============================================================
# Aba: Por produto
# ============================================================

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
            "auc_rf": lambda v: formata_moeda(v),
            "pct_estoque_rf": "{:.2f}%".format,
            "pct_auc_convexa": "{:.2f}%".format,
        })
    )

    st.bar_chart(
        grp_prod_sorted.set_index("tipo_produto")["auc_rf"],
        use_container_width=True
    )

# ============================================================
# Aba: Por classe
# ============================================================

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
            "auc_rf": lambda v: formata_moeda(v),
            "pct_estoque_rf": "{:.2f}%".format,
            "pct_auc_convexa": "{:.2f}%".format,
        })
    )

    st.bar_chart(
        grp_class_sorted.set_index("classe_rf")["auc_rf"],
        use_container_width=True
    )

# ============================================================
# Aba: Por emissor
# ============================================================

with aba_emissor:
    st.subheader("AuC de Renda Fixa por emissor")

    if "emissor" not in df.columns:
        st.info("A coluna 'Emissor' não foi encontrada no arquivo.")
    else:
        grp_emissor = (
            df.groupby("emissor", dropna=False)["valor_rf"]
            .sum()
            .reset_index()
            .rename(columns={"valor_rf": "auc_rf"})
        )

        grp_emissor["pct_estoque_rf"] = (
            grp_emissor["auc_rf"] / total_rf * 100 if total_rf > 0 else 0.0
        )
        grp_emissor["pct_auc_convexa"] = (
            grp_emissor["auc_rf"] / auc_total_convexa * 100
            if auc_total_convexa > 0
            else 0.0
        )

        grp_emissor_sorted = grp_emissor.sort_values("auc_rf", ascending=False)

        st.dataframe(
            grp_emissor_sorted.style.format(
                {
                    "auc_rf": lambda v: formata_moeda(v),
                    "pct_estoque_rf": "{:.2f}%".format,
                    "pct_auc_convexa": "{:.2f}%".format,
                }
            )
        )

        st.bar_chart(
            grp_emissor_sorted.set_index("emissor")["auc_rf"],
            use_container_width=True,
        )

# ============================================================
# Aba: Histórico
# ============================================================

with aba_hist:
    st.subheader("Histórico mês a mês")

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

    if salvar_historico:
        if os.path.exists(HIST_FILE):
            hist_antigo = pd.read_parquet(HIST_FILE)
            hist_novo = pd.concat([hist_antigo, resumo_dia], ignore_index=True)
        else:
            hist_novo = resumo_dia

        hist_novo.to_parquet(HIST_FILE, index=False)
        st.success("Histórico atualizado com a posição atual.")

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
