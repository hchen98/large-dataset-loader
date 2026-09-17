"""
Large Dataset Loader & Filter (performance build)
-------------------------------------------------
Upload CSV / Excel / Parquet (handles 150k+ rows, files > 200 MB), then
filter on any column value.

Run with uv:
    uv add streamlit pandas openpyxl pyarrow
    uv run streamlit run data_loader.py --server.maxUploadSize 2000

Or put this in .streamlit/config.toml so you don't pass the flag each time:
    [server]
    maxUploadSize = 2000

Performance features:
  - pyarrow CSV engine (much faster than the default parser on big files)
  - Parquet support (near-instant reads, dtypes preserved)
  - automatic dtype downcasting + category conversion (cuts memory 50-80%)
  - cached loading so filtering never re-reads the file
  - filter form: filtering runs only on "Apply", not on every widget change
  - only a preview is rendered in the browser; downloads use the full set
"""

import io
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Data Loader & Filter", layout="wide")

MAX_PREVIEW_ROWS = 1000  # only render this many rows in the browser table


# ----------------------------------------------------------------------
# Memory optimization
# ----------------------------------------------------------------------
def optimize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Downcast numerics and convert low-cardinality text to category."""
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_integer_dtype(s):
            df[col] = pd.to_numeric(s, downcast="integer")
        elif pd.api.types.is_float_dtype(s):
            df[col] = pd.to_numeric(s, downcast="float")
        elif pd.api.types.is_object_dtype(s):
            # convert to category only when it actually saves space.
            # Cast values to string first: object columns can hold a mix of
            # ints and strings, which produces mixed-type categories that
            # Arrow (used by st.dataframe) cannot convert.
            if len(s) and s.nunique(dropna=True) / len(s) < 0.5:
                df[col] = s.astype("string").astype("category")
            else:
                # high-cardinality: keep as-is but normalize to string dtype
                # so mixed int/str object columns still convert to Arrow
                df[col] = s.astype("string")
    return df


# ----------------------------------------------------------------------
# Loading (cached so filtering doesn't re-read the file every rerun)
# ----------------------------------------------------------------------
@st.cache_data(show_spinner="Loading file...")
def load_data(file_bytes: bytes, name: str, sheet) -> pd.DataFrame:
    buffer = io.BytesIO(file_bytes)
    lower = name.lower()

    if lower.endswith(".parquet"):
        df = pd.read_parquet(buffer)
    elif lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(buffer, sheet_name=sheet, engine="openpyxl")
    else:  # CSV / TSV
        sep = "\t" if lower.endswith(".tsv") else ","
        try:
            df = pd.read_csv(buffer, sep=sep, engine="pyarrow")
        except Exception:
            # pyarrow is strict about messy CSVs; fall back to the C parser
            buffer.seek(0)
            df = pd.read_csv(buffer, sep=sep, low_memory=False)

    return optimize_dtypes(df)


@st.cache_data(show_spinner=False)
def get_sheets(file_bytes: bytes) -> list:
    return pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl").sheet_names


@st.cache_data(show_spinner=False)
def to_excel(data: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        data.to_excel(w, index=False, sheet_name="filtered")
    return out.getvalue()


@st.cache_data(show_spinner=False)
def to_csv(data: pd.DataFrame) -> bytes:
    return data.to_csv(index=False).encode("utf-8")


# ----------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------
st.title("Data Loader & Filter")

uploaded = st.file_uploader(
    "Upload a CSV, TSV, Excel, or Parquet file",
    type=["csv", "tsv", "xlsx", "xls", "parquet"],
    help="Handles large datasets (150k+ rows). Only a preview is rendered; "
    "filtering runs on the full data.",
)

if uploaded is None:
    st.info("Upload a file to begin.")
    st.stop()

file_bytes = uploaded.getvalue()

# Excel sheet picker
sheet = 0
if uploaded.name.lower().endswith((".xlsx", ".xls")):
    sheets = get_sheets(file_bytes)
    if len(sheets) > 1:
        sheet = st.selectbox("Sheet", sheets)

df = load_data(file_bytes, uploaded.name, sheet)

mem_mb = df.memory_usage(deep=True).sum() / 1e6
st.success(
    f"Loaded {len(df):,} rows x {len(df.columns)} columns "
    f"- {mem_mb:,.1f} MB in memory"
)

# ----------------------------------------------------------------------
# Filtering - inside a form so it runs only on "Apply"
# ----------------------------------------------------------------------
st.sidebar.header("Filters")

with st.sidebar.form("filter_form"):
    cols_to_filter = st.multiselect("Columns to filter on", options=list(df.columns))

    # collect each column's control value; apply after submit
    controls = {}
    for col in cols_to_filter:
        s = df[col]
        st.markdown(f"**{col}**")

        if pd.api.types.is_numeric_dtype(s):
            lo, hi = float(s.min()), float(s.max())
            if lo == hi:
                st.caption(f"single value: {lo}")
            else:
                controls[col] = ("num", st.slider(
                    f"Range - {col}", lo, hi, (lo, hi), key=f"num_{col}"
                ))

        elif pd.api.types.is_datetime64_any_dtype(s):
            lo, hi = s.min(), s.max()
            controls[col] = ("date", st.date_input(
                f"Range - {col}", (lo, hi), key=f"date_{col}"
            ))

        else:  # categorical / text
            n_unique = s.nunique(dropna=True)
            if n_unique <= 200:
                controls[col] = ("cat", st.multiselect(
                    f"Values - {col}",
                    sorted(s.dropna().astype(str).unique()),
                    key=f"cat_{col}",
                ))
            else:
                controls[col] = ("txt", st.text_input(
                    f"Contains - {col} (too many values for a list)",
                    key=f"txt_{col}",
                ))

    submitted = st.form_submit_button("Apply filters")

# Build a boolean mask (fast: no intermediate copies)
mask = pd.Series(True, index=df.index)
for col, (kind, val) in controls.items():
    if kind == "num":
        mask &= df[col].between(val[0], val[1])
    elif kind == "date" and len(val) == 2:
        mask &= df[col].between(pd.Timestamp(val[0]), pd.Timestamp(val[1]))
    elif kind == "cat" and val:
        mask &= df[col].astype(str).isin(val)
    elif kind == "txt" and val:
        mask &= df[col].astype(str).str.contains(val, case=False, na=False)

filtered = df[mask]

# ----------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------
st.subheader("Results")
st.write(f"{len(filtered):,} of {len(df):,} rows match")

if len(filtered) > MAX_PREVIEW_ROWS:
    st.caption(f"Showing first {MAX_PREVIEW_ROWS:,} rows. Download for the full set.")
st.dataframe(filtered.head(MAX_PREVIEW_ROWS), use_container_width=True)

c1, c2, c3 = st.columns(3)
c1.download_button(
    "Download CSV", to_csv(filtered), file_name="filtered.csv", mime="text/csv"
)
c2.download_button(
    "Download Excel",
    to_excel(filtered),
    file_name="filtered.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
c3.download_button(
    "Download Parquet",
    filtered.to_parquet(index=False),
    file_name="filtered.parquet",
    mime="application/octet-stream",
)
