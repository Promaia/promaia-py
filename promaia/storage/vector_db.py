"""
Vector Database Manager using ChromaDB for semantic search.

Handles embedding generation (OpenAI with sentence-transformers fallback)
and ChromaDB operations for content storage and retrieval.
"""
import os
from typing import List, Dict, Any, Optional
import logging

# Load environment first
from promaia.utils.config import load_environment
load_environment()

# Suppress ChromaDB's noisy warnings about existing embeddings during searches
logging.getLogger('chromadb.segment.impl.vector.local_persistent_hnsw').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class VectorDBManager:
    """
    Manages ChromaDB operations and embedding generation.
    
    One-to-one mapping: 1 page_id = 1 markdown file = 1 vector embedding
    """
    
    def __init__(self, chroma_path: str = None):
        """
        Initialize ChromaDB client and embedding function.
        
        Args:
            chroma_path: Path to ChromaDB directory (not a file)
        """
        if chroma_path is None:
            from promaia.utils.env_writer import get_data_dir
            chroma_path = os.path.join(get_data_dir(), "chroma_db")
        self.chroma_path = chroma_path
        self.collection_name = "promaia_content"
        
        # Initialize ChromaDB
        try:
            import chromadb
            
            # Ensure the directory exists
            os.makedirs(chroma_path, exist_ok=True)
            
            # Initialize client - use basic initialization for ChromaDB 0.5.x
            self.client = chromadb.PersistentClient(path=chroma_path)
            
            # Get or create collection with cosine similarity (distance function)
            # Note: ChromaDB 0.5.x uses 'l2', 'ip' (inner product), or 'cosine'
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}  # Use cosine similarity
            )

            # Initialize property collection for property-specific embeddings
            # Increased HNSW parameters to handle large collections with complex filters
            self.property_collection = self.client.get_or_create_collection(
                name="promaia_properties",
                metadata={
                    "hnsw:space": "cosine",
                    "hnsw:construction_ef": 200,  # Construction time parameter (default: 100)
                    "hnsw:search_ef": 100,        # Search time parameter (default: 10) - key fix
                    "hnsw:M": 32                  # Max connections per element (default: 16)
                }
            )

            logger.info(f"✅ ChromaDB initialized at {chroma_path}")
            logger.info("✅ Property embeddings collection initialized")
        except Exception as e:
            import traceback
            logger.error(f"❌ Failed to initialize ChromaDB: {e}")
            logger.error(traceback.format_exc())
            raise
        
        # Initialize embedding function
        self._init_embedding_function()
    
    def _init_embedding_function(self):
        """Initialize OpenAI embedding model."""
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is required for embeddings. "
                "Set it in your .env file or run 'maia setup'."
            )
        from openai import OpenAI
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.embedding_provider = "openai"
        self.embedding_model = "text-embedding-3-small"
        logger.info(f"Using OpenAI embeddings: {self.embedding_model}")
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for given text.
        
        Args:
            text: Input text to embed
            
        Returns:
            List of floats representing the embedding vector
        """
        if not text or not text.strip():
            raise ValueError("Cannot generate embedding for empty text")
        
        try:
            if self.embedding_provider == "openai":
                response = self.openai_client.embeddings.create(
                    input=text,
                    model=self.embedding_model
                )
                return response.data[0].embedding
            
            else:
                raise RuntimeError(f"Unknown embedding provider: {self.embedding_provider}")
        
        except Exception as e:
            logger.error(f"❌ Embedding generation failed: {e}")
            raise
    
    def add_content(
        self,
        page_id: str,
        content_text: str,
        metadata: Dict[str, Any]
    ) -> bool:
        """
        Add content to ChromaDB with embedding.
        
        Args:
            page_id: Unique identifier (from unified_content)
            content_text: Full markdown content to embed
            metadata: Metadata dict with database_name, workspace, created_time, etc.
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate inputs before calling ChromaDB
            if not content_text or not isinstance(content_text, str):
                logger.warning(f"Invalid content_text for {page_id}, skipping vector embedding")
                return False

            if not page_id or not isinstance(page_id, str):
                logger.warning(f"Invalid page_id, skipping vector embedding")
                return False

            # Generate embedding
            embedding = self.generate_embedding(content_text)

            # Validate embedding
            if embedding is None or not isinstance(embedding, list) or len(embedding) == 0:
                logger.warning(f"Invalid embedding generated for {page_id}, skipping")
                return False

            # Check for NaN or inf values that would crash hnswlib
            try:
                import math
                if any(not math.isfinite(x) for x in embedding):
                    logger.warning(f"Embedding for {page_id} contains NaN or inf values, skipping")
                    return False
            except (TypeError, ValueError) as e:
                logger.warning(f"Invalid embedding values for {page_id}: {e}, skipping")
                return False

            # Remove None values and ensure valid types (ChromaDB requires str, int, float, or bool)
            clean_metadata = {}
            for k, v in metadata.items():
                if v is None:
                    continue
                if isinstance(v, (str, int, float, bool)):
                    clean_metadata[k] = v
                elif isinstance(v, (list, dict)):
                    # Convert complex types to strings
                    clean_metadata[k] = str(v)
                else:
                    # Skip unsupported types
                    logger.debug(f"Skipping metadata key '{k}' with unsupported type {type(v)}")

            # Upsert to collection (updates existing or inserts new) with extra safety
            try:
                self.collection.upsert(
                    ids=[page_id],
                    documents=[content_text],
                    embeddings=[embedding],
                    metadatas=[clean_metadata]
                )
                logger.debug(f"✅ Upserted embedding for page_id: {page_id}")
                return True
            except Exception as chroma_error:
                # ChromaDB/hnswlib can crash with certain data
                # Log the error but return False to allow sync to continue
                logger.error(f"ChromaDB error adding {page_id}: {chroma_error}")
                logger.warning(f"Skipping vector embedding for {page_id} due to ChromaDB error")
                return False

        except Exception as e:
            logger.error(f"❌ Failed to add content for {page_id}: {e}")
            return False
    
    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for given text.
        
        Args:
            text: Input text to estimate tokens for
            
        Returns:
            Estimated token count
        """
        if self.embedding_provider == "openai":
            try:
                import tiktoken
                encoding = tiktoken.get_encoding("cl100k_base")
                return len(encoding.encode(text))
            except ImportError:
                logger.warning("tiktoken not available, using rough estimation")
                return len(text) // 4
        else:
            # Rough estimation for other providers
            return len(text) // 4
    
    def add_content_with_chunking(
        self,
        page_id: str,
        content_text: str,
        metadata: Dict[str, Any],
        chunks: List[Dict[str, Any]]
    ) -> bool:
        """
        Add content to ChromaDB with chunking support.
        
        Embeds each chunk separately with chunk-specific metadata.
        
        Args:
            page_id: Unique page identifier
            content_text: Full markdown content (not used, chunks used instead)
            metadata: Base metadata dict (database_name, workspace, etc.)
            chunks: List of chunk dicts from page_chunker.chunk_page_content()
                    Each contains: chunk_id, content, chunk_index, total_chunks, etc.
            
        Returns:
            True if all chunks embedded successfully, False otherwise
        """
        try:
            # First, remove any existing embeddings for this page
            # (including old chunks or non-chunked versions)
            try:
                self.collection.delete(where={"page_id": page_id})
            except:
                pass  # May not exist, that's okay
            
            # Embed each chunk
            success_count = 0
            for chunk in chunks:
                try:
                    chunk_id = chunk['chunk_id']
                    chunk_content = chunk['content']
                    
                    # Generate embedding for this chunk
                    embedding = self.generate_embedding(chunk_content)
                    
                    # Prepare chunk-specific metadata
                    chunk_metadata = {
                        **metadata,  # Include base metadata
                        'page_id': page_id,  # Store original page_id for retrieval
                        'chunk_id': chunk_id,
                        'chunk_index': chunk['chunk_index'],
                        'total_chunks': chunk['total_chunks'],
                        'is_chunk': True,  # Flag to indicate this is a chunk
                        'estimated_tokens': chunk.get('estimated_tokens', 0)
                    }

                    # Remove None values from metadata (ChromaDB requires str, int, float, or bool)
                    chunk_metadata = {k: v for k, v in chunk_metadata.items() if v is not None}

                    # Upsert to collection (updates existing or inserts new) with chunk_id as the ID
                    self.collection.upsert(
                        ids=[chunk_id],
                        documents=[chunk_content],
                        embeddings=[embedding],
                        metadatas=[chunk_metadata]
                    )

                    success_count += 1
                    logger.debug(f"✅ Upserted chunk {chunk['chunk_index'] + 1}/{chunk['total_chunks']} for page {page_id}")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to embed chunk {chunk.get('chunk_id')}: {e}")
                    # Continue with other chunks even if one fails
            
            if success_count == len(chunks):
                logger.info(f"✅ Successfully embedded all {success_count} chunks for page {page_id}")
                return True
            elif success_count > 0:
                logger.warning(f"⚠️  Partially embedded {success_count}/{len(chunks)} chunks for page {page_id}")
                return True  # Consider partial success as success
            else:
                logger.error(f"❌ Failed to embed any chunks for page {page_id}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Failed to add chunked content for {page_id}: {e}")
            return False

    def add_property_embedding(
        self,
        page_id: str,
        property_name: str,
        property_value: str,
        property_type: str,
        base_metadata: Dict[str, Any]
    ) -> bool:
        """
        Add property-specific embedding to separate collection.

        Args:
            page_id: Base page ID
            property_name: Column name (e.g., "epic", "status")
            property_value: Formatted text value to embed
            property_type: Notion type (e.g., "relation", "select")
            base_metadata: Base metadata (workspace, database_name, etc.)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Generate embedding
            embedding = self.generate_embedding(property_value)

            # Create unique ID: page123_prop_epic
            vector_id = f"{page_id}_prop_{property_name}"

            # Metadata for filtering (filter out None values - ChromaDB doesn't accept them)
            metadata = {
                **base_metadata,
                "page_id": page_id,
                "property_name": property_name,
                "property_type": property_type
            }

            # Remove None values from metadata (ChromaDB requires str, int, float, or bool)
            metadata = {k: v for k, v in metadata.items() if v is not None}

            # Upsert to property collection (updates existing or inserts new)
            self.property_collection.upsert(
                ids=[vector_id],
                documents=[property_value],
                embeddings=[embedding],
                metadatas=[metadata]
            )

            logger.debug(f"✅ Upserted property embedding: {vector_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to add property embedding for {page_id}.{property_name}: {e}")
            return False

    def delete_property_embedding(
        self,
        page_id: str,
        property_name: str
    ) -> bool:
        """
        Delete a single property embedding.

        Args:
            page_id: Base page ID
            property_name: Property column name

        Returns:
            True if deleted, False otherwise
        """
        try:
            vector_id = f"{page_id}_prop_{property_name}"

            # Check if exists first
            existing = self.property_collection.get(ids=[vector_id])
            if not existing['ids']:
                logger.debug(f"Property embedding not found: {vector_id}")
                return False

            # Delete from collection
            self.property_collection.delete(ids=[vector_id])
            logger.debug(f"🗑️ Deleted property embedding: {vector_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to delete property embedding {page_id}.{property_name}: {e}")
            return False

    def delete_property_embeddings(
        self,
        property_name: str,
        database_id: str = None,
        workspace: str = None,
        database_name: str = None
    ) -> int:
        """
        Delete all embeddings for a specific property.

        Args:
            property_name: Property column name to delete
            database_id: Optional database ID filter
            workspace: Optional workspace filter
            database_name: Optional database name filter

        Returns:
            Number of embeddings deleted
        """
        try:
            # Build filter
            where_filter = {"property_name": property_name}
            if database_id:
                where_filter["database_id"] = database_id
            if workspace:
                where_filter["workspace"] = workspace
            if database_name:
                where_filter["database_name"] = database_name

            # Get all matching embeddings
            results = self.property_collection.get(
                where=where_filter,
                include=[]  # Only need IDs
            )

            if not results['ids']:
                logger.debug(f"No property embeddings found for {property_name}")
                return 0

            # Delete all matching embeddings
            count = len(results['ids'])
            self.property_collection.delete(ids=results['ids'])

            logger.info(f"🗑️ Deleted {count} property embeddings for {property_name}")
            return count

        except Exception as e:
            logger.error(f"❌ Failed to delete property embeddings for {property_name}: {e}")
            return 0

    def search_property(
        self,
        property_name: str,
        query_text: str,
        filters: Optional[Dict[str, Any]] = None,
        n_results: int = 20,
        min_similarity: float = 0.2
    ) -> List[Dict[str, Any]]:
        """
        Search specific property embeddings.

        Args:
            property_name: Property to search (e.g., "epic", "title")
            query_text: Semantic query text
            filters: Additional metadata filters (workspace, database_name)
            n_results: Max results
            min_similarity: Minimum similarity threshold

        Returns:
            List of results with page_id and similarity scores
        """
        try:
            # Generate query embedding
            query_embedding = self.generate_embedding(query_text)

            # Build combined filters
            # Always use $and when combining property_name with other filters
            if filters:
                # Check if filters contains operators (e.g., $and, $or)
                if any(key.startswith('$') for key in filters.keys()):
                    # Filters has operators, wrap both in $and
                    combined_filters = {
                        "$and": [
                            {"property_name": property_name},
                            filters
                        ]
                    }
                else:
                    # Flat dict - convert each key-value to separate condition
                    filter_conditions = [{"property_name": property_name}]
                    for key, value in filters.items():
                        filter_conditions.append({key: value})
                    combined_filters = {"$and": filter_conditions}
            else:
                # No additional filters, just property_name
                combined_filters = {"property_name": property_name}

            # Search property collection
            results = self.property_collection.query(
                query_embeddings=[query_embedding],
                where=combined_filters,
                n_results=n_results
            )

            # Format results
            formatted_results = []
            if results['ids'] and results['ids'][0]:
                for i, doc_id in enumerate(results['ids'][0]):
                    distance = results['distances'][0][i]
                    similarity = 1 - distance

                    if similarity >= min_similarity:
                        formatted_results.append({
                            'id': doc_id,
                            'page_id': results['metadatas'][0][i]['page_id'],
                            'similarity_score': similarity,
                            'metadata': results['metadatas'][0][i],
                            'property_value': results['documents'][0][i]
                        })

            logger.info(f"🔍 Property search '{property_name}' returned {len(formatted_results)} results")
            return formatted_results

        except Exception as e:
            logger.error(f"❌ Property search failed: {e}")
            return []

    def search(
        self,
        query_text: str,
        filters: Optional[Dict[str, Any]] = None,
        n_results: int = 20,
        min_similarity: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Search for similar content using semantic similarity.
        
        Args:
            query_text: Search query text
            filters: Metadata filters (e.g., {"workspace": "trass", "database_name": {"$in": [...]}})
            n_results: Maximum number of results to return
            min_similarity: Minimum similarity score (0-1, cosine distance)
            
        Returns:
            List of dicts with page_id, distance (similarity), and metadata
        """
        try:
            # Generate query embedding
            query_embedding = self.generate_embedding(query_text)
            
            # Build query parameters
            query_params = {
                "query_embeddings": [query_embedding],
                "n_results": n_results
            }
            
            # Add metadata filters if provided
            if filters:
                query_params["where"] = filters
            
            # Execute search
            results = self.collection.query(**query_params)
            
            # Format results
            formatted_results = []
            if results and results['ids'] and len(results['ids']) > 0:
                for i, page_id in enumerate(results['ids'][0]):
                    distance = results['distances'][0][i] if results['distances'] else 0
                    similarity_score = 1 - distance  # Convert distance to similarity
                    
                    # Filter by minimum similarity
                    if similarity_score >= min_similarity:
                        formatted_results.append({
                            'page_id': page_id,
                            'similarity_score': similarity_score,
                            'distance': distance,
                            'metadata': results['metadatas'][0][i] if results['metadatas'] else {}
                        })
            
            logger.info(f"🔍 Vector search returned {len(formatted_results)} results")
            return formatted_results
        
        except Exception as e:
            logger.error(f"❌ Vector search failed: {e}")
            return []
    
    def check_exists(self, page_id: str) -> bool:
        """
        Check if a page_id already exists in ChromaDB.
        
        Args:
            page_id: The page_id to check
            
        Returns:
            True if exists, False otherwise
        """
        try:
            result = self.collection.get(ids=[page_id])
            return len(result['ids']) > 0
        except Exception as e:
            logger.debug(f"Check exists failed for {page_id}: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the vector database.
        
        Returns:
            Dict with collection stats
        """
        try:
            count = self.collection.count()
            return {
                "collection_name": self.collection_name,
                "total_documents": count,
                "embedding_provider": self.embedding_provider,
                "embedding_model": self.embedding_model,
                "chroma_path": self.chroma_path
            }
        except Exception as e:
            logger.error(f"❌ Failed to get stats: {e}")
            return {}


# Convenience function for easy import
def get_vector_db_manager(chroma_path: str = None) -> VectorDBManager:
    """Get or create a VectorDBManager instance."""
    return VectorDBManager(chroma_path=chroma_path)

