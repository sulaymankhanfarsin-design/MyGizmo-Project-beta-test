import os
import io
import zipfile
from flask import Flask, render_template, request, jsonify, json, send_file
import qrcode
from slugify import slugify
from PIL import Image
from pdf2image import convert_from_bytes

# Initialize the Flask application
app = Flask(__name__)

# Define the folder where static files (like QR codes) will be saved
STATIC_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static')
# Make sure the static folder exists
if not os.path.exists(STATIC_FOLDER):
    os.makedirs(STATIC_FOLDER)

# --- File Converter Helper Functions ---

def convert_jpg_to_pdf(image_files):
    """Converts one or MORE JPG image files to a SINGLE PDF."""
    
    pil_images = []
    for image_file in image_files:
        image = Image.open(image_file)
        # Ensure image is in RGB mode (PDFs don't like some modes)
        if image.mode == 'RGBA' or image.mode == 'P':
            image = image.convert('RGB')
        pil_images.append(image)

    # Check if we have any images
    if not pil_images:
        return None # No images to convert

    # Create a PDF in memory
    pdf_buffer = io.BytesIO()
    
    # Save the first image, and append the rest
    pil_images[0].save(
        pdf_buffer, 
        format='PDF', 
        save_all=True, 
        append_images=pil_images[1:] # Add all other images
    )
    
    pdf_buffer.seek(0) # Rewind the buffer to the beginning
    return pdf_buffer

def convert_pdf_to_jpgs(pdf_file):
    """Converts a PDF (which could have many pages) into a list of JPG images."""
    # Read the PDF file's bytes
    pdf_bytes = pdf_file.read()
    
    # This is where Poppler is used.
    # You MUST install Poppler on your system for this to work.
    try:
        images = convert_from_bytes(pdf_bytes)
    except Exception as e:
        print(f"PDF to JPG Error: {e}")
        print("This often means Poppler is not installed or not in your system's PATH.")
        return None

    # Create a zip file in memory to hold the images
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zf:
        for i, image in enumerate(images):
            # Save each image to a temporary in-memory buffer
            img_buffer = io.BytesIO()
            image.save(img_buffer, format='JPEG')
            img_buffer.seek(0)
            # Add the image to the zip file
            zf.writestr(f'page_{i+1}.jpg', img_buffer.read())
            
    zip_buffer.seek(0)
    return zip_buffer
# --- End of File Converter Functions ---


# --- ROUTE: Homepage ---
@app.route('/')
def home():
    return render_template('home.html')

# --- ROUTE: QR Code Generator ---
@app.route('/qr-generator', methods=['GET', 'POST'])
def qr_generator():
    qr_image_path = None  # Variable to hold the path to the QR image

    if request.method == 'POST':
        url = request.form['url']
        
        if url: # Check if the URL is not empty
            img = qrcode.make(url)
            qr_filename = 'qr_code_generated.png'
            qr_image_path_full = os.path.join(STATIC_FOLDER, qr_filename)
            
            # Save the image
            img.save(qr_image_path_full)
            
            # Pass just the filename to the template
            qr_image_path = qr_filename

    return render_template('qr_generator.html', qr_image_path=qr_image_path)
    
# --- ROUTE: Calculator ---
@app.route('/calculator')
def calculator():
    return render_template('calculator.html')

# --- ROUTE: Slug Generator ---
@app.route('/slug-generator')
def slug_generator():
    """Renders the new slug generator page."""
    return render_template('slug_generator.html')

@app.route('/generate-slug', methods=['POST'])
def generate_slug():
    """The API endpoint for generating the slug."""
    data = request.json
    
    text = data.get('text', '')
    separator = data.get('separator', '-')
    remove_numbers = data.get('remove_numbers', False)
    lowercase = data.get('lowercase', True) 
    
    final_slug = slugify(
        text, 
        separator=separator,
        lowercase=lowercase
    )

    if remove_numbers:
        final_slug = "".join(c for c in final_slug if not c.isdigit())
        # Clean up any double-separators that might result
        final_slug = final_slug.replace(separator * 2, separator)
        if final_slug.startswith(separator):
            final_slug = final_slug[1:]
        if final_slug.endswith(separator):
            final_slug = final_slug[:-1]

    return jsonify({'slug': final_slug})

# --- ROUTE: List Randomizer ---
@app.route('/list-randomizer')
def list_randomizer():
    """Renders the new list randomizer page."""
    return render_template('list_randomizer.html')

# --- ROUTES: File Converter ---
@app.route('/file-converter')
def file_converter():
    """Renders the new file converter page."""
    return render_template('file_converter.html')

@app.route('/convert', methods=['POST'])
def handle_conversion():
    """Handles the file upload and conversion logic from the converter tool."""
    
    # Use .getlist() to get multiple files
    files = request.files.getlist('file')
    
    if not files or files[0].filename == '':
        return "No selected file", 400
        
    conversion_type = request.form['conversion_type']

    if conversion_type == 'jpg_to_pdf':
        try:
            pdf_buffer = convert_jpg_to_pdf(files)
            if pdf_buffer:
                return send_file(
                    pdf_buffer,
                    as_attachment=True,
                    download_name='converted.pdf',
                    mimetype='application/pdf'
                )
            else:
                return "No valid JPG images found", 400
        except Exception as e:
            return f"Error during JPG to PDF conversion: {e}", 500

    elif conversion_type == 'pdf_to_jpg':
        try:
            # PDF to JPG only ever takes one file
            if len(files) > 1:
                return "Please upload only one PDF for PDF-to-JPG conversion.", 400
            
            zip_buffer = convert_pdf_to_jpgs(files[0])
            if zip_buffer:
                return send_file(
                    zip_buffer,
                    as_attachment=True,
                    download_name='converted_images.zip',
                    mimetype='application/zip'
                )
            else:
                return "Error during PDF to JPG conversion. Did you install Poppler?", 500
        except Exception as e:
            return f"Error during PDF to JPG conversion: {e}. (Did you install Poppler?)", 500

    return "Invalid conversion type", 400
# --- End of File Converter Routes ---


# This runs the app
if __name__ == '__main__':
    app.run(debug=True)