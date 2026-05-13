from rag_service import load_documents


if __name__ == "__main__":
    print("Indexando documentos...")
    total_chunks = load_documents(reset=False)
    print(f"Indexacion completada. Chunks disponibles: {total_chunks}")
