# Large Dataset Loader & Filter

A Streamlit app for loading large tabular datasets (150k+ rows, files over 200 MB) and filtering them by column value. Upload a CSV, TSV, Excel, or Parquet file, apply filters in the sidebar, and download the filtered result.

## Features

- Handles large files — reads with the pyarrow engine and downcasts dtypes to cut memory 50–80%
- Supports CSV, TSV, Excel (`.xlsx`/`.xls`, with sheet picker), and Parquet
- Per-column filters that adapt to the data type: range sliders for numbers and dates, multiselect for categories, and text "contains" search for high-cardinality columns
- Filters run only when applied, not on every widget change
- Renders a preview in the browser while filtering and downloads use the full dataset
- Export results as CSV, Excel, or Parquet

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv add streamlit pandas openpyxl pyarrow
```

## Usage

```bash
uv run streamlit run data_loader.py --server.maxUploadSize 2000
```

The `--server.maxUploadSize` flag (value in MB) raises Streamlit's default 200 MB upload cap. To make it permanent, create `.streamlit/config.toml`:

```toml
[server]
maxUploadSize = 2000
```

Then run without the flag:

```bash
uv run streamlit run data_loader.py
```

## Notes

- Uploaded files are held in memory and pandas needs several times the file size again to parse them, so a multi-GB file can require substantial RAM.
- For repeated use on the same data, converting it to Parquet once makes reloads far faster.
