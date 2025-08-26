from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pdf2image import convert_from_bytes
from google.cloud import vision
import pytesseract
from PIL import Image
import os
import base64

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = vision.ImageAnnotatorClient()

@app.post("/upload")
async def upload(file: UploadFile = File(...), authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized")

    contents = await file.read()
    text = ""

    try:
        if file.filename.lower().endswith(".pdf"):
            images = convert_from_bytes(contents)
            text = "\n".join(pytesseract.image_to_string(img) for img in images)
        else:
            image = Image.open(file.file)
            text = pytesseract.image_to_string(image)
    except:
        text = ""

    if len(text) < 100:
        print("👀 Falling back to Google Cloud Vision")
        gcv_input = base64.b64encode(contents).decode("utf-8")
        result = client.document_text_detection({"content": gcv_input})
        text = result[0].full_text_annotation.text

        return JSONResponse({"text": text, "source": "gcv"})

    return JSONResponse({"text": text, "source": "tesseract"})
