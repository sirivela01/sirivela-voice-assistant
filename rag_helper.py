import os
import json
import math
from pypdf import PdfReader

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

def dot_product(v1, v2):
    return sum(x * y for x, y in zip(v1, v2))

def magnitude(v):
    return math.sqrt(sum(x * x for x in v))

def cosine_similarity(v1, v2):
    if HAS_NUMPY:
        try:
            arr1 = np.array(v1, dtype=np.float32)
            arr2 = np.array(v2, dtype=np.float32)
            norm1 = np.linalg.norm(arr1)
            norm2 = np.linalg.norm(arr2)
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return float(np.dot(arr1, arr2) / (norm1 * norm2))
        except Exception as e:
            # Fallback on numpy failure
            pass
            
    dot = dot_product(v1, v2)
    mag1 = magnitude(v1)
    mag2 = magnitude(v2)
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)

def chunk_text(text, max_chars=600, overlap=100):
    chunks = []
    text = text.replace('\r\n', '\n').strip()
    if not text:
        return chunks
        
    start = 0
    while start < len(text):
        end = start + max_chars
        if end < len(text):
            # Look back for a whitespace boundary to keep sentences/words intact
            last_space = text.rfind(' ', start + max_chars - 80, end)
            if last_space != -1:
                end = last_space
                
        chunk = text[start:end].strip()
        if len(chunk) > 10:
            chunks.append(chunk)
            
        start = end - overlap
        if start >= len(text) or end >= len(text):
            break
            
    return chunks

def extract_pdf_text(filepath):
    try:
        reader = PdfReader(filepath)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n".join(text_parts)
    except Exception as e:
        print(f"RAG Error: Failed reading PDF {filepath}: {e}")
        return ""

def load_vector_store(store_path):
    if os.path.exists(store_path):
        try:
            with open(store_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"RAG Warning: Error loading vector store: {e}")
    return {"files_metadata": {}, "chunks": []}

def save_vector_store(store_path, data):
    try:
        with open(store_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"RAG Error: Error saving vector store: {e}")

def init_rag(client, base_dir=None):
    """
    Initialize the RAG store: scans documents/, indexes new or changed files,
    generates embeddings using Gemini API, and saves them locally.
    """
    if base_dir is None:
        base_dir = os.path.abspath(os.path.dirname(__file__))
        
    docs_dir = os.path.join(base_dir, 'documents')
    os.makedirs(docs_dir, exist_ok=True)
    
    store_path = os.path.join(base_dir, 'vector_store.json')
    store = load_vector_store(store_path)
    
    # Scan documents folder for supported formats
    current_files = {}
    if os.path.exists(docs_dir):
        for filename in os.listdir(docs_dir):
            filepath = os.path.join(docs_dir, filename)
            if os.path.isfile(filepath) and filename.lower().endswith(('.txt', '.pdf', '.md')):
                current_files[filename] = os.path.getmtime(filepath)
                
    metadata = store.setdefault("files_metadata", {})
    chunks_list = store.setdefault("chunks", [])
    
    # 1. Clean up chunks of deleted files
    deleted_files = [fn for fn in list(metadata.keys()) if fn not in current_files]
    if deleted_files:
        print(f"RAG: Removing deleted files from index: {deleted_files}")
        chunks_list = [c for c in chunks_list if c.get("filename") not in deleted_files]
        store["chunks"] = chunks_list
        for fn in deleted_files:
            if fn in metadata:
                del metadata[fn]
            
    # 2. Check for new or modified files
    files_to_index = []
    for filename, mtime in current_files.items():
        if filename not in metadata or metadata[filename].get("mtime") != mtime:
            files_to_index.append((filename, mtime))
            
    if not files_to_index:
        print("RAG: No new or modified files detected. Index is up to date.")
        # Make sure structure is saved properly
        save_vector_store(store_path, store)
        return
        
    print(f"RAG: Files to index: {[f[0] for f in files_to_index]}")
    
    # Remove previous chunks for modified files
    modified_filenames = [f[0] for f in files_to_index if f[0] in metadata]
    if modified_filenames:
        chunks_list = [c for c in chunks_list if c.get("filename") not in modified_filenames]
        store["chunks"] = chunks_list
        
    for filename, mtime in files_to_index:
        filepath = os.path.join(docs_dir, filename)
        
        # Extract text content
        if filename.lower().endswith('.pdf'):
            text = extract_pdf_text(filepath)
        else:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    text = f.read()
            except Exception as e:
                print(f"RAG Error: Could not read {filename}: {e}")
                continue
                
        # Split into semantic chunks
        file_chunks = chunk_text(text)
        if not file_chunks:
            metadata[filename] = {"mtime": mtime}
            continue
            
        print(f"RAG: Generating embeddings for {filename} ({len(file_chunks)} chunks)...")
        
        # Batch embed content to minimize API calls
        embeddings = []
        batch_size = 50
        for i in range(0, len(file_chunks), batch_size):
            batch = file_chunks[i:i+batch_size]
            if client is None:
                print("RAG Warning: Client is not configured. Mocking embeddings.")
                batch_embeddings = [[0.0] * 768 for _ in batch]
            else:
                try:
                    resp = client.models.embed_content(
                        model='gemini-embedding-2',
                        contents=batch
                    )
                    batch_embeddings = [e.values for e in resp.embeddings]
                except Exception as e:
                    print(f"RAG Error: Embedding call failed: {e}")
                    batch_embeddings = [[0.0] * 768 for _ in batch]
            embeddings.extend(batch_embeddings)
            
        # Add to index list
        for idx, (chunk_text_content, emb) in enumerate(zip(file_chunks, embeddings)):
            chunks_list.append({
                "filename": filename,
                "text": chunk_text_content,
                "embedding": emb
            })
            
        metadata[filename] = {"mtime": mtime}
        
    store["chunks"] = chunks_list
    save_vector_store(store_path, store)
    print("RAG: Local indexing finished.")

def get_relevant_context(client, query, num_results=3, similarity_threshold=0.35, base_dir=None):
    """
    Search the RAG store for the most relevant document chunks matching the query.
    Returns a list of dicts with keys: 'filename', 'text', 'similarity'.
    """
    if base_dir is None:
        base_dir = os.path.abspath(os.path.dirname(__file__))
        
    store_path = os.path.join(base_dir, 'vector_store.json')
    if not os.path.exists(store_path):
        return []
        
    store = load_vector_store(store_path)
    chunks = store.get("chunks", [])
    if not chunks:
        return []
        
    if client is None:
        print("RAG Warning: Gemini client is not initialized, cannot perform search.")
        return []
        
    try:
        resp = client.models.embed_content(
            model='gemini-embedding-2',
            contents=query
        )
        query_embedding = resp.embeddings[0].values
    except Exception as e:
        print(f"RAG Error: Failed to embed search query: {e}")
        return []
        
    scored_chunks = []
    for chunk in chunks:
        emb = chunk.get("embedding")
        if not emb or len(emb) != len(query_embedding):
            continue
        sim = cosine_similarity(query_embedding, emb)
        if sim >= similarity_threshold:
            scored_chunks.append((sim, chunk))
            
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    
    results = []
    for sim, chunk in scored_chunks[:num_results]:
        results.append({
            "filename": chunk["filename"],
            "text": chunk["text"],
            "similarity": sim
        })
        
    return results
