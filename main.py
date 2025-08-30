import os
import json
import tempfile
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import uvicorn

# Try importing Google Vision (optional)
try:
    from google.cloud import vision
    GOOGLE_VISION_AVAILABLE = True
except ImportError:
    GOOGLE_VISION_AVAILABLE = False

app = FastAPI(
    title="BlocIQ OCR Service",
    description="Lightweight OCR service for property management platform",
    version="1.0.0"
)

# Get allowed origins from environment variable or use defaults
allowed_origins = os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else [
    "https://www.blociq.co.uk",
    "https://blociq-h3xv-bf7j9j1tw-eleanoroxley-9774s-projects.vercel.app",
    "https://*.vercel.app",
    "http://localhost:3000"
]

# Clean up any empty strings from the list
allowed_origins = [origin.strip() for origin in allowed_origins if origin.strip()]

print(f"CORS configured for origins: {allowed_origins}")

# Add CORS middleware with explicit configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Initialize Google Vision client if credentials are available
vision_client = None
if GOOGLE_VISION_AVAILABLE:
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if credentials_json:
        try:
            # Parse credentials JSON and create client
            credentials_dict = json.loads(credentials_json)
            # Write credentials to temporary file for Google client
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(credentials_dict, f)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
            vision_client = vision.ImageAnnotatorClient()
            print("Google Vision client initialized successfully")
        except Exception as e:
            print(f"Failed to initialize Google Vision: {e}")

def extract_text_with_tesseract(image_path: str) -> str:
    """Extract text using Tesseract OCR"""
    try:
        image = Image.open(image_path)
        # Configure Tesseract for better accuracy
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(image, config=custom_config)
        return text.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tesseract OCR failed: {str(e)}")

def extract_text_with_google_vision(image_path: str) -> str:
    """Extract text using Google Vision API"""
    if not vision_client:
        raise HTTPException(status_code=500, detail="Google Vision not configured")
    
    try:
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        response = vision_client.text_detection(image=image)
        
        if response.error.message:
            raise Exception(f"Google Vision API error: {response.error.message}")
        
        texts = response.text_annotations
        if texts:
            return texts[0].description.strip()
        else:
            return ""
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google Vision OCR failed: {str(e)}")

def process_pdf(file_path: str, use_google_vision: bool = False) -> tuple[str, str]:
    """Process PDF file and extract text from all pages"""
    try:
        # Convert PDF to images
        images = convert_from_path(file_path, dpi=300)
        extracted_texts = []
        
        for i, image in enumerate(images):
            # Save image temporarily
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_img:
                image.save(temp_img.name, 'PNG')
                
                # Extract text from this page
                if use_google_vision and vision_client:
                    page_text = extract_text_with_google_vision(temp_img.name)
                    source = "google-vision"
                else:
                    page_text = extract_text_with_tesseract(temp_img.name)
                    source = "tesseract"
                
                if page_text.strip():
                    extracted_texts.append(f"--- Page {i+1} ---\n{page_text}")
                
                # Clean up temporary image
                os.unlink(temp_img.name)
        
        combined_text = "\n\n".join(extracted_texts)
        return combined_text, source
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF processing failed: {str(e)}")

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "message": "BlocIQ OCR Service is running",
        "tesseract_available": True,
        "google_vision_available": vision_client is not None,
        "allowed_origins": allowed_origins
    }

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    use_google_vision: Optional[bool] = False
):
    """
    Upload and process a file for OCR
    
    - **file**: PDF or image file to process
    - **use_google_vision**: Use Google Vision API instead of Tesseract (requires credentials)
    """
    
    print(f"Processing file: {file.filename}, content_type: {file.content_type}")
    
    # Validate file type
    allowed_types = {
        'application/pdf': ['.pdf'],
        'image/jpeg': ['.jpg', '.jpeg'],
        'image/png': ['.png'],
        'image/tiff': ['.tiff', '.tif'],
        'image/bmp': ['.bmp']
    }
    
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file type: {file.content_type}. Supported types: {list(allowed_types.keys())}"
        )
    
    # Check if Google Vision is requested but not available
    if use_google_vision and not vision_client:
        raise HTTPException(
            status_code=400, 
            detail="Google Vision API requested but not configured. Please set GOOGLE_CREDENTIALS_JSON environment variable."
        )
    
    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_file_path = temp_file.name
    
    try:
        # Process based on file type
        if file.content_type == 'application/pdf':
            extracted_text, source = process_pdf(temp_file_path, use_google_vision)
        else:
            # Process image file
            if use_google_vision and vision_client:
                extracted_text = extract_text_with_google_vision(temp_file_path)
                source = "google-vision"
            else:
                extracted_text = extract_text_with_tesseract(temp_file_path)
                source = "tesseract"
        
        print(f"OCR completed: {len(extracted_text)} characters extracted using {source}")
        
        return {
            "text": extracted_text,
            "source": source,
            "filename": file.filename,
            "content_type": file.content_type
        }
        
    finally:
        # Clean up temporary file
        os.unlink(temp_file_path)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)