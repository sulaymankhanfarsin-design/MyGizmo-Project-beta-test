from flask import Flask, render_template, request
import qrcode
import os

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
# This is the logic from your OLD app.py
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

# This allows you to run the app by just running `python app.py`
    
    # --- ADD THIS NEW ROUTE FOR THE CALCULATOR ---
@app.route('/calculator')
def calculator():
    # This just renders the calculator.html template
    return render_template('calculator.html')
# --- END OF NEW ROUTE ---

if __name__ == '__main__':
    app.run(debug=True)