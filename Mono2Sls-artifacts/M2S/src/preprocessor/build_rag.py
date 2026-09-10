import argparse
import os
import json
from pathlib import Path
from typing import List, Dict, Any

from llama_index.core import Document, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MONOLITH_DIR = ROOT_DIR / "input_monolith"
DEFAULT_INDEX_DIR = ROOT_DIR / "storage" / "code_index"
DEFAULT_ANALYSIS_REPORT = ROOT_DIR / "storage" / "analysis_report.json"


def load_analysis_report(report_path: Path) -> Dict[str, Any]:
    """Load the static analysis report; symbol_table is loaded from symbol_table.json in the same directory."""
    if not report_path.exists():
        print(f"Warning: Analysis report not found at {report_path}. Using empty report.")
        return {}
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        if "symbol_table" not in report:
            st_path = report_path.parent / "symbol_table.json"
            if st_path.exists():
                with open(st_path, "r", encoding="utf-8") as f:
                    report["symbol_table"] = json.load(f)
                print(f"Loaded {len(report['symbol_table'])} symbols from {st_path}")

        return report
    except Exception as e:
        print(f"Error loading analysis report: {e}")
        return {}


def extract_code_chunk(source: str, start_line: int, end_line: int) -> str:
    """
    Extract a line-range slice from source code.

    Args:
        source: Full source code string.
        start_line: First line to include (1-based).
        end_line: Last line to include (1-based).

    Returns:
        Extracted code snippet as a string.
    """
    lines = source.splitlines()
    # start_line and end_line are 1-based; convert to 0-based slice indices.
    chunk_lines = lines[start_line - 1 : end_line]
    return "\n".join(chunk_lines)


SLIDING_WINDOW_LINES = 50    # lines per sliding-window chunk
SLIDING_WINDOW_OVERLAP = 10  # overlap between adjacent chunks


def sliding_window_chunks(source: str, rel_path: str, window: int = SLIDING_WINDOW_LINES,
                           overlap: int = SLIDING_WINDOW_OVERLAP) -> List[Document]:
    """
    Apply fixed-size sliding-window chunking to files that have no function-level symbols.

    Produces uniformly-sized chunks so that their vector-similarity scores are
    comparable to function-level chunks from symbol-table-annotated files.
    Each chunk is [window - overlap, window] lines long.
    """
    lines = source.splitlines()
    total = len(lines)
    if total == 0:
        return []

    docs: List[Document] = []
    step = max(1, window - overlap)
    start = 0

    while start < total:
        end = min(start + window, total)
        chunk_lines = lines[start:end]
        chunk_text = "\n".join(chunk_lines)

        if chunk_text.strip():  # skip all-whitespace chunks
            docs.append(Document(
                text=chunk_text,
                metadata={
                    "file_path": rel_path,
                    "function_name": "window_chunk",
                    "type": "window",
                    "start_line": start + 1,   # 1-based
                    "end_line": end
                }
            ))

        if end >= total:
            break
        start += step

    return docs


def build_documents(monolith_root: Path, analysis_report: Dict[str, Any]) -> List[Document]:
    """
    Walk source code and build RAG documents using the static-analysis symbol table.

    - Files with function/class symbols -> chunked at symbol granularity.
    - Files without symbols -> sliding-window chunking (fixed line count, with overlap).

    Both strategies produce similarly-sized chunks so retrieval scores are
    comparable across chunk types, without requiring a file blacklist.
    """
    docs: List[Document] = []
    symbol_table = analysis_report.get("symbol_table", [])

    # Build rel_path -> symbols mapping.
    # Note: file_path in symbol_table may carry an app_name prefix (see static_analyzer.py),
    # so we must support both prefixed and un-prefixed forms.
    file_symbols: Dict[str, List[Dict[str, Any]]] = {}
    for symbol in symbol_table:
        fp = symbol["file_path"]
        if fp not in file_symbols:
            file_symbols[fp] = []
        file_symbols[fp].append(symbol)

    allowed_ext = {".py", ".js", ".ts", ".jsx", ".tsx", ".vue"}

    print(f"Scanning files in {monolith_root}...")
    print(f"Symbol table contains {len(symbol_table)} symbols across {len(file_symbols)} files")
    print(f"Sliding window config: {SLIDING_WINDOW_LINES} lines / {SLIDING_WINDOW_OVERLAP} overlap")

    stats = {
        "total_files": 0,
        "symbol_chunked_files": 0,
        "window_chunked_files": 0,
        "symbol_chunks": 0,
        "window_chunks": 0,
    }

    # Directories to ignore (kept in sync with static_analyzer.py).
    ignore_dirs = {
        'venv', 'env', '.venv', '__pycache__', 'node_modules',
        '.git', '.idea', '.vscode', 'dist', 'build',
        '.pytest_cache', '.mypy_cache', 'htmlcov', '.tox',
        'eggs', '.eggs', 'coverage'
    }

    for dirpath, dirnames, filenames in os.walk(monolith_root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith('.')]

        for name in filenames:
            if name.startswith("."):
                continue

            file_path = Path(dirpath) / name
            ext = file_path.suffix.lower()

            if ext not in allowed_ext:
                continue

            rel_path = file_path.relative_to(monolith_root).as_posix()

            stats["total_files"] += 1

            try:
                with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
                continue

            # static_analyzer prefixes rel_path with app_name (e.g., "bookstore/routes/auth.js")
            # but os.walk produces un-prefixed paths (e.g., "routes/auth.js").
            # Look up both forms to handle either convention.
            app_name = monolith_root.name
            prefixed_rel_path = f"{app_name}/{rel_path}"

            symbols = file_symbols.get(prefixed_rel_path) or file_symbols.get(rel_path) or []

            if symbols:
                # Has symbols: chunk at function/class granularity.
                for symbol in symbols:
                    chunk_text = extract_code_chunk(source, symbol["start_line"], symbol["end_line"])
                    symbol_id = symbol["id"]
                    function_name = symbol_id.split(".")[-1]

                    docs.append(Document(
                        text=chunk_text,
                        metadata={
                            "file_path": prefixed_rel_path,
                            "function_name": function_name,
                            "symbol_id": symbol_id,
                            "type": symbol.get("kind", "function"),
                            "start_line": symbol["start_line"],
                            "end_line": symbol["end_line"]
                        }
                    ))
                    stats["symbol_chunks"] += 1

                stats["symbol_chunked_files"] += 1
            else:
                # No symbols: fall back to sliding-window chunking.
                window_docs = sliding_window_chunks(source, prefixed_rel_path)
                docs.extend(window_docs)
                stats["window_chunked_files"] += 1
                stats["window_chunks"] += len(window_docs)

    print(f"\n=== Indexing Statistics (Strategy: Symbol + Sliding Window) ===")
    print(f"Total files scanned: {stats['total_files']}")
    print(f"  - Symbol-level chunked : {stats['symbol_chunked_files']} files "
          f"→ {stats['symbol_chunks']} chunks")
    print(f"  - Window chunked       : {stats['window_chunked_files']} files "
          f"→ {stats['window_chunks']} chunks  "
          f"(window={SLIDING_WINDOW_LINES}, overlap={SLIDING_WINDOW_OVERLAP})")
    print(f"\nTotal documents indexed: {len(docs)}")
    print(f"================================================================\n")

    return docs


def build_and_persist_index(monolith_root: Path, index_dir: Path, analysis_report: Dict[str, Any]) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)

    documents = build_documents(monolith_root, analysis_report)
    if not documents:
        print(f"No documents found in {monolith_root}")
        return

    print(f"Building index for {len(documents)} code chunks...")
    
    # Use CodeBERT embeddings for semantic code search.
    embed_model = HuggingFaceEmbedding(model_name="microsoft/codebert-base")

    index = VectorStoreIndex.from_documents(documents, embed_model=embed_model)
    index.storage_context.persist(persist_dir=str(index_dir))

    print(f"RAG index built successfully! Persisted to {index_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RAG index over monolith source code")
    parser.add_argument(
        "--monolith-root",
        type=str,
        default=str(DEFAULT_MONOLITH_DIR),
        help="Monolith root directory",
    )
    parser.add_argument(
        "--index-dir",
        type=str,
        default=str(DEFAULT_INDEX_DIR),
        help="Directory to persist the vector index",
    )
    parser.add_argument(
        "--analysis-report",
        type=str,
        default=str(DEFAULT_ANALYSIS_REPORT),
        help="Path to static analysis report json",
    )

    args = parser.parse_args()

    monolith_root = Path(args.monolith_root).resolve()
    index_dir = Path(args.index_dir).resolve()
    report_path = Path(args.analysis_report).resolve()

    if not monolith_root.exists():
        raise SystemExit(f"Monolith root not found: {monolith_root}")

    # Load the static analysis report.
    analysis_report = load_analysis_report(report_path)
    
    build_and_persist_index(monolith_root, index_dir, analysis_report)


if __name__ == "__main__":
    main()
