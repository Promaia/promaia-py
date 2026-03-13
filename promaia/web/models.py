from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union

class ImageData(BaseModel):
    """Represents an image attachment in a chat message."""
    data: str  # Base64 encoded image data
    media_type: str  # MIME type (e.g., 'image/jpeg', 'image/png', 'image/webp')
    filename: Optional[str] = None  # Optional filename

class MessageContent(BaseModel):
    """Represents the content of a chat message, which can include text and/or images."""
    text: Optional[str] = None
    images: Optional[List[ImageData]] = None

    def __str__(self):
        """For backward compatibility when content is used as string."""
        return self.text or ""

class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant'
    content: Union[str, MessageContent]  # Backward compatible - can be string or MessageContent
    timestamp: Optional[str] = None
    
    def get_text_content(self) -> str:
        """Get the text content regardless of content type."""
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, MessageContent):
            return self.content.text or ""
        return ""
    
    def get_images(self) -> List[ImageData]:
        """Get the image attachments."""
        if isinstance(self.content, MessageContent) and self.content.images:
            return self.content.images
        return []
    
    def has_images(self) -> bool:
        """Check if this message has image attachments."""
        return len(self.get_images()) > 0

class ChatMessageInput(BaseModel):
    message: str
    images: Optional[List[ImageData]] = None  # Direct image attachments for input
    conversation_id: Optional[str] = None
    history: Optional[List[ChatMessage]] = None
    preferred_model: Optional[str] = None  # Specific model ID like 'claude-opus-4-5', 'gemini-3-pro-preview', etc. Falls back to provider type for backwards compatibility.

class TokenUsage(BaseModel):
    prompt_tokens: int
    response_tokens: int
    total_tokens: int
    cost: Optional[float] = None  # Total cost in USD
    model: Optional[str] = None  # Model name for cost calculation context

class ChatMessageOutput(BaseModel):
    reply: str
    conversation_id: str
    model_used: Optional[str] = None  # Which model actually responded
    token_usage: Optional[TokenUsage] = None  # Token count information
    # We could add other fields like debug_info, etc. 

class InitialMessageOutput(BaseModel):
    message: str
    conversation_id: str
    model_used: Optional[str] = None 