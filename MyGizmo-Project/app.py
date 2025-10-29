from flask import Flask, render_template, request, jsonify, json
import qrcode
import os
from slugify import slugify  # <-- ADDED THIS IMPORT

# Initialize the Flask application
app = Flask(__name__)

# Define the folder where QR codes will be saved
STATIC_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static')
# Make sure the static folder exists
if not os.path.exists(STATIC_FOLDER):
    os.makedirs(STATIC_FOLDER)

# --- Route for the Homepage ---
@app.route('/')
def home():
    return render_template('home.html')

# --- Route for the QR Code Generator ---
@app.route('/qr-generator', methods=['GET', 'POST'])
def qr_generator():
    qr_image_path = None  # Variable to hold the path to the QR image

    if request.method == 'POST':
        url = request.form['url']
        
        if url: # Check if the URL is not empty
            img = qrcode.make(url)
            qr_filename = 'qr_code_generated.png' # Use a new name
            qr_image_path_full = os.path.join(STATIC_FOLDER, qr_filename)
            
            # Save the image
            img.save(qr_image_path_full)
            
            # Pass just the filename to the template
            qr_image_path = qr_filename

    # Render the NEW qr_generator.html page
    return render_template('qr_generator.html', qr_image_path=qr_image_path)
    
# --- Route for the Calculator ---
@app.route('/calculator')
def calculator():
    # This just renders the calculator.html template
    return render_template('calculator.html')

# --- START: NEW SLUG GENERATOR ROUTES ---

@app.route('/slug-generator')
def slug_generator():
    """Renders the new slug generator page."""
    return render_template('slug_generator.html')


@app.route('/generate-slug', methods=['POST'])
def generate_slug():
    """The API for generating the slug."""
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
        final_slug = final_slug.replace(separator * 2, separator)

    return jsonify({'slug': final_slug})

# --- END: NEW SLUG GENERATOR ROUTES ---


if __name__ == '__main__':
    app.run(debug=True)