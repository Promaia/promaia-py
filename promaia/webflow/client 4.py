"""
Webflow API client for interacting with the Webflow CMS.
"""
import os
import json
import requests
import mimetypes
import random
import string
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def truncate_url(url: str, max_length: int = 60) -> str:
    """Truncate a URL for display in logs."""
    if not url or len(url) <= max_length:
        return url
    
    # Split into parts
    parts = url.split('://')
    if len(parts) < 2:
        return url[:max_length-3] + '...'
    
    protocol = parts[0]
    rest = parts[1]
    
    # Calculate how much space we have left
    remaining = max_length - len(protocol) - 6  # 6 = len('://...') + len('...')
    
    # If not enough space, just do basic truncation
    if remaining < 10:
        return url[:max_length-3] + '...'
    
    # Divide remaining space between start and end of the URL
    start_length = remaining // 2
    end_length = remaining - start_length
    
    return f"{protocol}://{rest[:start_length]}...{rest[-end_length:]}"

class WebflowClient:
    """
    Client for the Webflow API.
    """

    def __init__(self, silent: bool = False):
        """Initialize the Webflow client."""
        self.api_key = os.getenv("WEBFLOW_API_KEY")
        self.site_id = os.getenv("WEBFLOW_SITE_ID")

        if not self.api_key:
            raise ValueError("WEBFLOW_API_KEY environment variable not found. Please add it to your .env file.")

        if not self.site_id:
            raise ValueError("WEBFLOW_SITE_ID environment variable not found. Please add it to your .env file.")

        self.base_url = "https://api.webflow.com/v2"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "accept": "application/json",
            "Content-Type": "application/json"
        }
        self.silent = silent
        
        # Print authentication info for debugging
        # print(f"Webflow API authentication setup:")
        # print(f"  Site ID: {self.site_id}")
        # print(f"  API Key: {self.api_key[:5]}...{self.api_key[-5:]} (length: {len(self.api_key)})")
        # print(f"  Authorization header: {self.headers['Authorization'][:12]}...{self.headers['Authorization'][-5:]}")
    
    def get_collections(self) -> List[Dict[str, Any]]:
        """
        Get all collections from the Webflow site.
        
        Returns:
            List of collection objects
        """
        url = f"{self.base_url}/sites/{self.site_id}/collections"

        try:
            response = requests.get(url, headers=self.headers)

            if not response.ok and not self.silent:
                print(f"  Error response from Webflow: {response.status_code}")
                print(f"  Full error response: {response.text}")

            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"  Exception: {str(e)}")
            raise
    
    def get_collection_items(self, collection_id: str) -> List[Dict[str, Any]]:
        """
        Get all items from a specific collection with pagination support.
        
        Args:
            collection_id: ID of the collection
            
        Returns:
            List of all collection items (handles pagination automatically)
        """
        url = f"{self.base_url}/collections/{collection_id}/items"
        all_items = []
        offset = 0
        limit = 100

        while True:
            params = {
                "offset": offset,
                "limit": limit
            }

            try:
                response = requests.get(url, headers=self.headers, params=params)

                if not response.ok and not self.silent:
                    print(f"  Error response from Webflow: {response.status_code}")
                    print(f"  Full error response: {response.text}")

                response.raise_for_status()
                response_data = response.json()
                batch_items = response_data.get("items", [])

                if not batch_items:
                    break

                all_items.extend(batch_items)

                # Check if we got less than the limit, which means we're done
                if len(batch_items) < limit:
                    break

                offset += limit

            except Exception as e:
                if not self.silent:
                    print(f"  Exception: {str(e)}")
                raise
        return all_items
    
    def get_item(self, collection_id: str, item_id: str) -> Dict[str, Any]:
        """
        Get a specific item from a collection.
        
        Args:
            collection_id: ID of the collection
            item_id: ID of the item to retrieve
            
        Returns:
            Item object or None if not found
        """
        url = f"{self.base_url}/collections/{collection_id}/items/{item_id}"
        
        try:
            response = requests.get(url, headers=self.headers)
            
            if response.status_code == 404:
                return None
                
            if not response.ok:
                print(f"  Error response from Webflow: {response.status_code}")
                print(f"  Response body: {response.text}")
                return None
            
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  Request error: {str(e)}")
            return None
    
    def create_item(self, collection_id: str, item_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new item in a collection that will be immediately published to the live site.
        
        Args:
            collection_id: ID of the collection
            item_data: Data for the new item
            
        Returns:
            Created item object
        """
        url = f"{self.base_url}/collections/{collection_id}/items/live"
        
        # V2 API requires fieldData property in the correct format
        payload = {
            "isArchived": False,
            "isDraft": False,
            "fieldData": item_data
        }
        
        # Log the request payload
        print(f"  Creating live item with payload: {json.dumps(payload, indent=2)[:200]}...")
        
        try:
            response = requests.post(url, headers=self.headers, json=payload)
            
            if not response.ok:
                print(f"  Error response from Webflow: {response.status_code}")
                print(f"  Response body: {response.text}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  Request error: {str(e)}")
            raise
    
    def update_item(self, collection_id: str, item_id: str, item_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing item in a collection and publish it live immediately.
        
        Args:
            collection_id: ID of the collection
            item_id: ID of the item to update
            item_data: Updated data for the item
            
        Returns:
            Updated item object
        """
        # Use the single item live endpoint for V2 API
        url = f"{self.base_url}/collections/{collection_id}/items/{item_id}/live"
        
        # V2 API for single item update requires fieldData without an items array
        payload = {
            "isArchived": False,
            "isDraft": False,
            "fieldData": item_data
        }
        
        # Log the update operation
        if not self.silent:
            print(f"  Updating item {item_id}")
        
        try:
            # Use PATCH method for updating a single item in V2 API
            response = requests.patch(url, headers=self.headers, json=payload)
            
            if not response.ok:
                print(f"  ✗ Update failed: {response.status_code}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 404:
                print(f"  ✗ Item {item_id} not found in Webflow (404). It may have been deleted.")
                return None  # Let the caller handle creating a new item
            else:
                print(f"  Request error: {str(e)}")
                raise
        except requests.exceptions.RequestException as e:
            print(f"  Request error: {str(e)}")
            raise
    
    def delete_item(self, collection_id: str, item_id: str) -> bool:
        """
        Delete an item from a collection.
        
        Args:
            collection_id: ID of the collection
            item_id: ID of the item to delete
            
        Returns:
            Boolean indicating success (True) or failure (False)
        """
        # Use the single item endpoint for V2 API
        url = f"{self.base_url}/collections/{collection_id}/items/{item_id}"
        
        print(f"  Sending DELETE request to: {truncate_url(url)}")
        
        try:
            # Use DELETE method for V2 API
            response = requests.delete(url, headers=self.headers)
            
            if not response.ok:
                print(f"  Error deleting item: {response.status_code}")
                print(f"  Response body: {response.text}")
                return False
            
            # For successful deletion (204 No Content is common)
            if response.status_code == 204 or not response.text.strip():
                print(f"  Delete successful (empty response or 204 No Content)")
                return True
                
            # If we got here, it's a success response with some content
            print(f"  Delete successful with response: {response.text[:100]}")
            return True
            
        except Exception as e:
            print(f"  Delete error: {str(e)}")
            return False
    
    def find_item_by_slug(self, collection_id: str, slug: str) -> Optional[Dict[str, Any]]:
        """
        Find an item by its slug.
        
        Args:
            collection_id: ID of the collection
            slug: Slug to search for
            
        Returns:
            Item object if found, None otherwise
        """
        # Try first with a filter query approach
        url = f"{self.base_url}/collections/{collection_id}/items"
        params = {
            "offset": 0,
            "limit": 100
        }
        
        print(f"  Searching for item with slug '{slug}' in collection {collection_id}")
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            
            if not response.ok:
                print(f"  Error fetching items: {response.status_code}")
                return None
            
            items = response.json().get("items", [])
            
            # More efficient search through items
            for item in items:
                item_slug = item.get("fieldData", {}).get("slug", "")
                if item_slug == slug:
                    print(f"  Found item with matching slug: {item.get('id')}")
                    return item
            
            print(f"  No item found with slug '{slug}'")
            return None
        except Exception as e:
            print(f"  Error finding item by slug: {str(e)}")
            return None
    
    def get_site_info(self) -> Dict[str, Any]:
        """
        Get information about the current site.
        
        Returns:
            Site information object
        """
        url = f"{self.base_url}/sites/{self.site_id}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()
    
    def publish_site(self, domains: List[str] = None) -> Dict[str, Any]:
        """
        Publish the site to make all changes live.
        
        Args:
            domains: Optional list of domain IDs to publish to. If not provided, 
                    will publish to all domains associated with the site.
                    
        Returns:
            Response object with publish status
        """
        url = f"{self.base_url}/sites/{self.site_id}/publish"
        
        payload = {}
        if domains:
            payload["domains"] = domains
            
        print(f"Publishing site {self.site_id} to make all changes live...")
        response = requests.post(url, headers=self.headers, json=payload)
        
        if not response.ok:
            print(f"Error publishing site: {response.status_code}")
            print(f"Response body: {response.text}")
            
        response.raise_for_status()
        return response.json()
    
    def publish_collection_items(self, collection_id: str, item_ids: List[str] = None) -> Dict[str, Any]:
        """
        Publish multiple items in a collection at once.
        
        Args:
            collection_id: ID of the collection
            item_ids: Optional list of item IDs to publish. If not provided,
                     all items in the collection will be published.
                    
        Returns:
            Response object with publish status
        """
        url = f"{self.base_url}/collections/{collection_id}/items/live"
        
        payload = {}
        if item_ids:
            payload["itemIds"] = item_ids
            
        print(f"Publishing items in collection {collection_id}...")
        response = requests.post(url, headers=self.headers, json=payload)
        
        if not response.ok:
            print(f"Error publishing collection items: {response.status_code}")
            print(f"Response body: {response.text}")
            
        response.raise_for_status()
        return response.json()
    
    def publish_item(self, collection_id: str, item_id: str) -> Dict[str, Any]:
        """
        Publish a single item in a collection.
        
        Args:
            collection_id: ID of the collection
            item_id: ID of the item to publish
                    
        Returns:
            Response object with publish status
        """
        url = f"{self.base_url}/collections/{collection_id}/items/{item_id}/live"
        
        print(f"Publishing item {item_id} in collection {collection_id}...")
        response = requests.post(url, headers=self.headers)
        
        if not response.ok:
            print(f"Error publishing item: {response.status_code}")
            print(f"Response body: {response.text}")
            
        response.raise_for_status()
        return response.json()
    
    def get_collection_fields(self, collection_id: str) -> Dict[str, Any]:
        """
        Get the fields (schema) for a specific collection.
        
        Args:
            collection_id: ID of the collection
            
        Returns:
            Collection schema with field definitions
        """
        url = f"{self.base_url}/collections/{collection_id}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        data = response.json()
        
        # Extract fields from the collection
        fields = data.get("fields", [])
        
        # Format fields in a more readable way
        field_info = {}
        for field in fields:
            field_info[field.get("slug")] = {
                "name": field.get("name"),
                "type": field.get("type"),
                "required": field.get("required", False)
            }
        
        # In Webflow, 'name' and 'slug' are typically required
        # If they're not marked as required in the schema, mark them here
        if "name" in field_info and not field_info["name"]["required"]:
            field_info["name"]["required"] = True
        
        if "slug" in field_info and not field_info["slug"]["required"]:
            field_info["slug"]["required"] = True
        
        return field_info
    
    def upload_asset(self, file_path: str, file_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Upload an asset (image, file, etc.) to the Webflow asset library using the two-step process:
        1. Get upload URL from Webflow
        2. Upload file to Amazon S3
        
        Args:
            file_path: Path to the file to upload
            file_name: Optional name for the uploaded file
            
        Returns:
            Asset object if successful, None otherwise
        """
        # Use specified name or extract from path
        if not file_name:
            file_name = os.path.basename(file_path)
        
        try:
            import hashlib
            
            # Calculate MD5 hash of the file
            with open(file_path, 'rb') as f:
                file_data = f.read()

            # Determine content type from file path
            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = 'application/octet-stream'

            # Check if WebP conversion is enabled and should be applied
            from promaia.config.cms_config import should_convert_to_webp, get_webp_config
            if should_convert_to_webp(content_type):
                try:
                    from promaia.utils.image_processing import convert_to_webp
                    webp_config = get_webp_config()
                    quality = webp_config.get('quality', 85)
                    max_dimension = webp_config.get('max_dimension')

                    print(f"Converting {file_name} from {content_type} to WebP (quality={quality})")
                    original_size = len(file_data)
                    file_data, file_name = convert_to_webp(file_data, quality=quality, max_dimension=max_dimension)

                    # Calculate savings
                    new_size = len(file_data)
                    savings_percent = ((original_size - new_size) / original_size) * 100
                    print(f"WebP conversion: {original_size} → {new_size} bytes ({savings_percent:.1f}% smaller)")
                except Exception as e:
                    print(f"WebP conversion failed, using original: {e}")
                    # Continue with original file if conversion fails

            # Calculate hash after potential conversion
            file_hash = hashlib.md5(file_data).hexdigest()

            # Step 1: Get upload URL from Webflow
            url = f"{self.base_url}/sites/{self.site_id}/assets"
            
            # Create request payload
            payload = {
                "fileName": file_name,
                "fileHash": file_hash
            }
            
            # Make the request to get upload URL
            print(f"Getting upload URL for {file_name}...")
            response = requests.post(url, headers=self.headers, json=payload)
            
            if not response.ok:
                print(f"Error getting upload URL: {response.status_code}")
                print(f"Response body: {response.text}")
                return None
            
            # Parse response to get upload details
            upload_data = response.json()
            upload_url = upload_data.get('uploadUrl')
            upload_details = upload_data.get('uploadDetails', {})
            
            if not upload_url or not upload_details:
                print(f"Invalid response from Webflow API, missing upload URL or details")
                return None
            
            print(f"Got upload URL: {truncate_url(upload_url)}")
            
            # Step 2: Upload to S3
            # Prepare the form data for S3 upload
            form_data = {}
            
            # Add all the upload details as form fields
            for key, value in upload_details.items():
                form_data[key] = value
            
            # Add the file (use file_data which may have been converted to WebP)
            file_content_type = upload_details.get('content-type') or 'application/octet-stream'
            files = {
                'file': (file_name, file_data, file_content_type)
            }
            
            # Make the S3 upload request
            print(f"Uploading file to S3...")
            s3_response = requests.post(upload_url, data=form_data, files=files)
            
            if not s3_response.ok:
                print(f"Error uploading to S3: {s3_response.status_code}")
                print(f"Response body: {s3_response.text}")
                return None
            
            print(f"File uploaded successfully")
            
            # Return the original response from Webflow which contains URLs
            asset_url = upload_data.get('assetUrl') or upload_data.get('hostedUrl')
            print(f"Asset uploaded successfully: {truncate_url(asset_url)}")
            
            # Update the response with the URL field for compatibility
            upload_data['url'] = asset_url
            
            return upload_data
        except Exception as e:
            print(f"Exception uploading asset: {str(e)}")
            return None
    
    def _get_upload_url(self, file_name: str, content_type: str, file_size: int, file_data: bytes = None) -> Optional[Dict[str, Any]]:
        """
        Get an upload URL from Webflow for direct S3 upload.
        
        Args:
            file_name: Name of the file to upload
            content_type: Content type (MIME type) of the file
            file_size: Size of the file in bytes
            file_data: Binary data of the file (required for hash calculation)
            
        Returns:
            Upload data including URL and details
        """
        import hashlib
        
        url = f"{self.base_url}/sites/{self.site_id}/assets"
        
        # Calculate MD5 hash if file data is provided
        file_hash = None
        if file_data:
            file_hash = hashlib.md5(file_data).hexdigest()
        else:
            # For cases where we don't have the file data, create a dummy hash based on name and size
            # Note: This is not ideal but allows the API to proceed
            dummy_data = f"{file_name}{file_size}".encode('utf-8')
            file_hash = hashlib.md5(dummy_data).hexdigest()
        
        # Create request payload
        payload = {
            "fileName": file_name,
            "contentType": content_type,
            "fileSize": file_size,
            "fileHash": file_hash
        }
        
        try:
            # Make the request to get upload URL
            print(f"Requesting upload URL for {file_name} ({content_type}, {file_size} bytes)...")
            response = requests.post(url, headers=self.headers, json=payload)
            
            if not response.ok:
                print(f"Error getting upload URL: {response.status_code}")
                print(f"Response body: {response.text}")
                return None
            
            # Parse response to get upload details
            upload_data = response.json()
            upload_url = upload_data.get('uploadUrl')
            
            if not upload_url:
                print(f"Invalid response from Webflow API, missing upload URL")
                return None
            
            print(f"Got upload URL: {truncate_url(upload_url)}")
            return upload_data
            
        except Exception as e:
            print(f"Exception getting upload URL: {str(e)}")
            return None
            
    def upload_asset_from_url(self, image_url: str, file_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Upload an asset from a URL to Webflow.
        
        Args:
            image_url: URL of the image to upload
            file_name: Optional filename to use for the uploaded asset
            
        Returns:
            Asset data including the new URL
        """
        try:
            # Generate a unique filename if none provided
            if not file_name:
                # Extract extension from URL or content type
                try:
                    ext = os.path.splitext(image_url.split('?')[0])[1]
                    if not ext:
                        ext = '.jpg'  # Default to jpg if no extension
                except:
                    ext = '.jpg'
                
                # Generate random filename with the extracted extension
                random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                file_name = f"image_{random_str}{ext}"
            
            print(f"Uploading image from URL: {truncate_url(image_url)} as {file_name}")
            
            # Download the image
            print(f"Downloading image from {truncate_url(image_url)}")
            response = requests.get(image_url, stream=True)
            response.raise_for_status()
            
            # Determine content type from response or filename
            content_type = response.headers.get('Content-Type')
            if not content_type or 'text/html' in content_type:
                # Try to determine the content type from the filename
                content_type, _ = mimetypes.guess_type(file_name)
                if not content_type:
                    content_type = 'image/jpeg'  # Default to JPEG
            
            # Get file data and size
            file_data = response.content
            file_size = len(file_data)
            print(f"Image downloaded: {file_size} bytes, type: {content_type}")

            # Check if WebP conversion is enabled and should be applied
            from promaia.config.cms_config import should_convert_to_webp, get_webp_quality, get_webp_config
            if should_convert_to_webp(content_type):
                try:
                    from promaia.utils.image_processing import convert_to_webp
                    webp_config = get_webp_config()
                    quality = webp_config.get('quality', 85)
                    max_dimension = webp_config.get('max_dimension')

                    print(f"Converting {content_type} to WebP (quality={quality})")
                    original_size = file_size
                    file_data, file_name = convert_to_webp(file_data, quality=quality, max_dimension=max_dimension)
                    file_size = len(file_data)
                    content_type = 'image/webp'

                    # Calculate savings
                    savings_percent = ((original_size - file_size) / original_size) * 100
                    print(f"WebP conversion: {original_size} → {file_size} bytes ({savings_percent:.1f}% smaller)")
                except Exception as e:
                    print(f"WebP conversion failed, using original: {e}")
                    # Continue with original image if conversion fails

            # Get the upload URL from Webflow
            print(f"Getting upload URL from Webflow for {file_name}")
            upload_data = self._get_upload_url(file_name, content_type, file_size, file_data)
            
            if not upload_data or 'uploadUrl' not in upload_data:
                print(f"Failed to get upload URL from Webflow")
                return None
            
            upload_url = upload_data.get('uploadUrl')
            print(f"Got upload URL: {truncate_url(upload_url)}")
            
            # Step 2: Upload to S3
            print(f"Uploading to S3...")
            
            # Check if we have uploadDetails (needed for multipart form upload)
            if 'uploadDetails' in upload_data:
                # Multipart form upload approach (old API)
                form_data = {}
                
                # Add all the upload details as form fields
                for key, value in upload_data.get('uploadDetails', {}).items():
                    form_data[key] = value
                
                # Add the file
                files = {
                    'file': (file_name, file_data, content_type)
                }
                
                # Make the S3 upload request (POST with form data)
                s3_upload_response = requests.post(upload_url, data=form_data, files=files)
            else:
                # Direct upload approach (newer API)
                # Use PUT with content and headers
                headers = {
                    'Content-Type': content_type,
                }
                
                s3_upload_response = requests.put(upload_url, data=file_data, headers=headers)
            
            if not s3_upload_response.ok:
                print(f"S3 upload failed: {s3_upload_response.status_code}")
                print(f"Response: {s3_upload_response.text[:500]}")
                return None
            
            print(f"S3 upload successful")
            
            # Return the original response from Webflow which contains URLs
            asset_url = upload_data.get('assetUrl') or upload_data.get('hostedUrl') or upload_data.get('url')
            print(f"Asset uploaded successfully: {truncate_url(asset_url)}")
            
            # Update the response with the URL field for compatibility
            upload_data['url'] = asset_url
            return upload_data
            
        except Exception as e:
            print(f"Error uploading asset from URL: {str(e)}")
            return None

# Lazy-loaded client instance
_webflow_client_instance = None

def get_webflow_client(silent: bool = False):
    """Get the webflow client instance, creating it if needed."""
    global _webflow_client_instance
    if _webflow_client_instance is None or _webflow_client_instance.silent != silent:
        _webflow_client_instance = WebflowClient(silent=silent)
    return _webflow_client_instance 