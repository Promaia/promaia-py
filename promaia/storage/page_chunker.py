"""
Page Chunking Module for Large Notion Pages.

Implements hybrid chunking that splits large pages by blocks (respecting timestamps)
while enforcing token limits to ensure compatibility with embedding models.
"""
import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def estimate_tokens(text: str, provider: str = "openai") -> int:
    """
    Estimate token count for given text.
    
    Args:
        text: Input text to estimate tokens for
        provider: Embedding provider ("openai" or other)
        
    Returns:
        Estimated token count
    """
    if provider == "openai":
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            logger.warning("tiktoken not available, using rough estimation")
            # Fallback to rough estimation
            return len(text) // 4
    else:
        # Rough estimation for other providers
        return len(text) // 4


def split_markdown_by_blocks(markdown_content: str) -> List[str]:
    """
    Split markdown content into logical blocks.
    
    Splits on:
    - Headers (# ## ### etc.)
    - Double newlines (paragraphs)
    - Code blocks
    - Lists
    
    Args:
        markdown_content: Full markdown text
        
    Returns:
        List of markdown blocks
    """
    if not markdown_content:
        return []
    
    blocks = []
    current_block = []
    in_code_block = False
    
    lines = markdown_content.split('\n')
    
    for line in lines:
        # Check for code block boundaries
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            current_block.append(line)
            if not in_code_block:
                # End of code block, save it
                blocks.append('\n'.join(current_block))
                current_block = []
            continue
        
        # If in code block, just accumulate
        if in_code_block:
            current_block.append(line)
            continue
        
        # Check for headers (natural break points)
        if line.strip().startswith('#'):
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            current_block.append(line)
            continue
        
        # Empty line - potential paragraph break
        if not line.strip():
            if current_block:
                current_block.append(line)
                # Check if this is a significant break (2+ empty lines)
                if len(current_block) > 1 and not current_block[-2].strip():
                    blocks.append('\n'.join(current_block))
                    current_block = []
            continue
        
        # Regular content line
        current_block.append(line)
    
    # Add remaining content
    if current_block:
        blocks.append('\n'.join(current_block))
    
    # Filter out empty blocks
    blocks = [b.strip() for b in blocks if b.strip()]
    
    return blocks


def split_text_at_sentences(text: str, max_tokens: int, provider: str = "openai") -> List[str]:
    """
    Split text at sentence boundaries to fit within token limit.
    
    Used as fallback when a single block exceeds token limit.
    
    Args:
        text: Text to split
        max_tokens: Maximum tokens per chunk
        provider: Embedding provider for token estimation
        
    Returns:
        List of text chunks within token limit
    """
    # Split on sentence boundaries
    sentences = re.split(r'([.!?]\s+)', text)
    
    chunks = []
    current_chunk = []
    current_tokens = 0
    
    for i in range(0, len(sentences), 2):
        sentence = sentences[i]
        delimiter = sentences[i + 1] if i + 1 < len(sentences) else ''
        
        sentence_with_delimiter = sentence + delimiter
        sentence_tokens = estimate_tokens(sentence_with_delimiter, provider)
        
        # If single sentence exceeds limit, split by words
        if sentence_tokens > max_tokens:
            if current_chunk:
                chunks.append(''.join(current_chunk))
                current_chunk = []
                current_tokens = 0
            
            # Split long sentence by words
            words = sentence.split()
            word_chunk = []
            word_tokens = 0
            
            for word in words:
                word_tokens_est = estimate_tokens(word + ' ', provider)
                if word_tokens + word_tokens_est > max_tokens and word_chunk:
                    chunks.append(' '.join(word_chunk))
                    word_chunk = [word]
                    word_tokens = word_tokens_est
                else:
                    word_chunk.append(word)
                    word_tokens += word_tokens_est
            
            if word_chunk:
                chunks.append(' '.join(word_chunk) + delimiter)
            continue
        
        # Check if adding this sentence exceeds limit
        if current_tokens + sentence_tokens > max_tokens and current_chunk:
            chunks.append(''.join(current_chunk))
            current_chunk = [sentence_with_delimiter]
            current_tokens = sentence_tokens
        else:
            current_chunk.append(sentence_with_delimiter)
            current_tokens += sentence_tokens
    
    # Add remaining content
    if current_chunk:
        chunks.append(''.join(current_chunk))
    
    return chunks


def chunk_page_content(
    markdown_content: str,
    page_id: str,
    block_metadata: Optional[List[Dict]] = None,
    max_tokens: int = 6000,
    provider: str = "openai"
) -> List[Dict[str, Any]]:
    """
    Chunk page content using hybrid block+token-based approach.
    
    Strategy:
    1. Split markdown into logical blocks (headers, paragraphs)
    2. Group blocks by date boundaries if metadata available
    3. Ensure each chunk stays within token limit
    4. Fallback to sentence-level splitting if needed
    
    Args:
        markdown_content: Full markdown text to chunk
        page_id: Unique page identifier
        block_metadata: Optional list of block metadata with timestamps
        max_tokens: Maximum tokens per chunk (default 6000, safe margin below 8191)
        provider: Embedding provider for token estimation
        
    Returns:
        List of chunks with metadata:
        {
            'chunk_id': f"{page_id}_chunk_0",
            'page_id': page_id,
            'chunk_index': 0,
            'total_chunks': 3,
            'content': "chunk content...",
            'char_start': 0,
            'char_end': 5000,
            'estimated_tokens': 5500,
            'date_boundary': "2025-08-01"  # if applicable
        }
    """
    if not markdown_content or not markdown_content.strip():
        return []
    
    # Check if entire content fits in one chunk
    total_tokens = estimate_tokens(markdown_content, provider)
    if total_tokens <= max_tokens:
        # No chunking needed
        return [{
            'chunk_id': f"{page_id}_chunk_0",
            'page_id': page_id,
            'chunk_index': 0,
            'total_chunks': 1,
            'content': markdown_content,
            'char_start': 0,
            'char_end': len(markdown_content),
            'estimated_tokens': total_tokens,
            'date_boundary': None
        }]
    
    logger.info(f"Page {page_id} exceeds token limit ({total_tokens} > {max_tokens}), chunking...")
    
    # Split into blocks
    blocks = split_markdown_by_blocks(markdown_content)
    
    # Group blocks into chunks respecting token limits
    chunks = []
    current_chunk_blocks = []
    current_chunk_tokens = 0
    current_char_start = 0
    
    for block in blocks:
        block_tokens = estimate_tokens(block, provider)
        
        # If single block exceeds limit, split it further
        if block_tokens > max_tokens:
            # Save current chunk if any
            if current_chunk_blocks:
                chunk_content = '\n\n'.join(current_chunk_blocks)
                chunks.append({
                    'content': chunk_content,
                    'char_start': current_char_start,
                    'char_end': current_char_start + len(chunk_content),
                    'estimated_tokens': current_chunk_tokens,
                })
                current_char_start += len(chunk_content)
                current_chunk_blocks = []
                current_chunk_tokens = 0
            
            # Split large block at sentence boundaries
            sub_chunks = split_text_at_sentences(block, max_tokens, provider)
            for sub_chunk in sub_chunks:
                sub_tokens = estimate_tokens(sub_chunk, provider)
                chunks.append({
                    'content': sub_chunk,
                    'char_start': current_char_start,
                    'char_end': current_char_start + len(sub_chunk),
                    'estimated_tokens': sub_tokens,
                })
                current_char_start += len(sub_chunk)
            continue
        
        # Check if adding this block exceeds limit
        if current_chunk_tokens + block_tokens > max_tokens and current_chunk_blocks:
            # Save current chunk
            chunk_content = '\n\n'.join(current_chunk_blocks)
            chunks.append({
                'content': chunk_content,
                'char_start': current_char_start,
                'char_end': current_char_start + len(chunk_content),
                'estimated_tokens': current_chunk_tokens,
            })
            current_char_start += len(chunk_content)
            current_chunk_blocks = [block]
            current_chunk_tokens = block_tokens
        else:
            # Add block to current chunk
            current_chunk_blocks.append(block)
            current_chunk_tokens += block_tokens
    
    # Add remaining blocks
    if current_chunk_blocks:
        chunk_content = '\n\n'.join(current_chunk_blocks)
        chunks.append({
            'content': chunk_content,
            'char_start': current_char_start,
            'char_end': current_char_start + len(chunk_content),
            'estimated_tokens': current_chunk_tokens,
        })
    
    # Add chunk IDs and indices
    total_chunks = len(chunks)
    for i, chunk in enumerate(chunks):
        chunk['chunk_id'] = f"{page_id}_chunk_{i}"
        chunk['page_id'] = page_id
        chunk['chunk_index'] = i
        chunk['total_chunks'] = total_chunks
        chunk['date_boundary'] = None  # Can be enhanced with block_metadata later
    
    logger.info(f"Page {page_id} split into {total_chunks} chunks")
    
    return chunks
