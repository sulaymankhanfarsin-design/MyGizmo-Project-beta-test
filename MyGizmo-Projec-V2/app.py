import os
import io
import zipfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, jsonify, json, send_file, flash, redirect, url_for, session, send_from_directory
import qrcode
from slugify import slugify
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from pdf2image import convert_from_bytes
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from email_validator import validate_email
import stripe
from dotenv import load_dotenv
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from rembg import remove

load_dotenv() 

app = Flask(__name__)
app.secret_key = "a-very-secret-key-for-my-gizmo-business" 

# --- ফোল্ডার কনফিগারেশন ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
UPLOAD_FOLDER = os.path.join(STATIC_FOLDER, 'uploads_studio')
PROCESSED_FOLDER = os.path.join(STATIC_FOLDER, 'processed_studio')
# --- নতুন সংযোজন: ইউজার ফাইল সেভ করার ফোল্ডার ---
USER_FILES_FOLDER = os.path.join(STATIC_FOLDER, 'user_files')

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER
app.config['USER_FILES_FOLDER'] = USER_FILES_FOLDER # <-- নতুন কনফিগ
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- ডাটাবেস কনফিগারেশন ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)

# --- Stripe কী কনফিগারেশন ---
app.config['STRIPE_PUBLISHABLE_KEY'] = os.getenv('STRIPE_PUBLISHABLE_KEY')
app.config['STRIPE_SECRET_KEY'] = os.getenv('STRIPE_SECRET_KEY')
app.config['STRIPE_PRICE_ID'] = os.getenv('STRIPE_PRICE_ID')
stripe.api_key = app.config['STRIPE_SECRET_KEY']

# --- লগইন ম্যানেজার ---
login_manager = LoginManager(app)
login_manager.login_view = 'login' 
login_manager.login_message_category = 'info' 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ডাটাবেস মডেল (User Model) ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
    stripe_customer_id = db.Column(db.String(120), unique=True)
    subscription_status = db.Column(db.String(50), default='inactive')
    
    # --- নতুন সংযোজন: User এবং UserFile-এর মধ্যে সম্পর্ক ---
    files = db.relationship('UserFile', backref='user', lazy=True, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"User('{self.username}', '{self.email}', '{self.subscription_status}')"
    @property
    def password(self): raise AttributeError('password is not a readable attribute')
    @password.setter
    def password(self, password): self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    def verify_password(self, password): return bcrypt.check_password_hash(self.password_hash, password)

# --- নতুন সংযোজন: UserFile ডাটাবেস মডেল ---
class UserFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(300), nullable=False)
    saved_filename = db.Column(db.String(300), unique=True, nullable=False)
    file_type = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # User টেবিলের সাথে লিঙ্ক
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"UserFile('{self.original_filename}', '{self.file_type}')"


# --- ফর্ম ক্লাস (আগের মতোই) ---
class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=30)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')
    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user: raise ValidationError('That username is taken.')
    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user: raise ValidationError('That email is already in use.')
class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

# --- ফোল্ডার তৈরি ---
if not os.path.exists(STATIC_FOLDER): os.makedirs(STATIC_FOLDER)
if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(PROCESSED_FOLDER): os.makedirs(PROCESSED_FOLDER)
if not os.path.exists(USER_FILES_FOLDER): os.makedirs(USER_FILES_FOLDER) # <-- নতুন ফোল্ডার তৈরি

# --- টুলসের হেল্পার ফাংশন ---

# --- নতুন সংযোজন: ফাইল সেভ করার হেল্পার ফাংশন ---
def save_user_file(user, file_buffer_or_path, original_name, file_type):
    # যদি ইউজার লগইন করা না থাকে, তবে কিছুই সেভ করবে না
    if not user or not user.is_authenticated:
        return

    # আপনার নির্দেশনা অনুযায়ী, আমরা এখন 'pro' স্ট্যাটাস চেক করছি না
    # if user.subscription_status != 'active':
    #     return # ভবিষ্যতে এটি চালু করা যাবে

    try:
        unique_filename = f"{uuid.uuid4().hex}_{original_name}"
        save_path = os.path.join(app.config['USER_FILES_FOLDER'], unique_filename)
        
        # চেক করুন এটি একটি ফাইল পাথ নাকি মেমরি বাফার
        if isinstance(file_buffer_or_path, (str, Path)):
            # এটি একটি ফাইল পাথ, শুধু কপি করুন
            import shutil
            shutil.copy(file_buffer_or_path, save_path)
        elif hasattr(file_buffer_or_path, 'read'):
            # এটি একটি মেমরি বাফার (যেমন BytesIO)
            file_buffer_or_path.seek(0)
            with open(save_path, 'wb') as f:
                f.write(file_buffer_or_path.read())
            file_buffer_or_path.seek(0) # বাফারটি রিসেট করুন যাতে send_file এটি ব্যবহার করতে পারে
        
        # ডাটাবেসে এন্ট্রি তৈরি করুন
        new_file = UserFile(
            original_filename=original_name,
            saved_filename=unique_filename,
            file_type=file_type,
            user_id=user.id
        )
        db.session.add(new_file)
        db.session.commit()
        print(f"File saved for user {user.id}: {unique_filename}")

    except Exception as e:
        print(f"Error saving user file: {e}")
        db.session.rollback() # কোনো সমস্যা হলে ডাটাবেস রোলব্যাক করুন

# --- (বাকি হেল্পার ফাংশনগুলো আগের মতোই) ---
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "bmp", "gif"}
FORMAT_MAP = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "PDF": "pdf"}
def convert_jpg_to_pdf(image_files):
    pil_images = []
    for image_file in image_files:
        try:
            image = Image.open(image_file)
            if image.mode == 'RGBA' or image.mode == 'P': image = image.convert('RGB')
            pil_images.append(image)
        except Exception as e: print(f"Skipping non-image file: {e}")
    if not pil_images: return None
    pdf_buffer = io.BytesIO()
    pil_images[0].save(pdf_buffer, format='PDF', save_all=True, append_images=pil_images[1:])
    pdf_buffer.seek(0)
    return pdf_buffer
def convert_pdf_to_jpgs(pdf_file):
    pdf_bytes = pdf_file.read()
    try: images = convert_from_bytes(pdf_bytes)
    except Exception as e: print(f"PDF to JPG Error: {e} (Poppler installed?)"); return None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zf:
        for i, image in enumerate(images):
            img_buffer = io.BytesIO(); image.save(img_buffer, format='JPEG'); img_buffer.seek(0)
            zf.writestr(f'page_{i+1}.jpg', img_buffer.read())
    zip_buffer.seek(0); return zip_buffer
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT
def _safe_font(size=24):
    try: return ImageFont.truetype("arial.ttf", size)
    except IOError:
        try: return ImageFont.truetype("DejaVuSans.ttf", size)
        except IOError: return ImageFont.load_default()
def add_text_watermark(img: Image.Image, text: str, position: str, opacity: float, fontsize: int):
    if not text: return img
    base = img.convert("RGBA"); overlay = Image.new("RGBA", base.size, (255, 255, 255, 0)); draw = ImageDraw.Draw(overlay); font = _safe_font(fontsize)
    try:
        bbox = draw.textbbox((0, 0), text, font=font); textwidth = bbox[2] - bbox[0]; textheight = bbox[3] - bbox[1]
    except AttributeError: textwidth, textheight = draw.textsize(text, font=font)
    margin = max(10, int(min(base.size) * 0.02))
    positions = {"bottom-right": (base.width - textwidth - margin, base.height - textheight - margin),"bottom-left": (margin, base.height - textheight - margin),"top-left": (margin, margin),"top-right": (base.width - textwidth - margin, margin),"center": ((base.width - textwidth) // 2, (base.height - textheight) // 2),}
    x, y = positions.get(position, positions["bottom-right"]); fill = (255, 255, 255, int(255 * float(opacity))); draw.text((x, y), text, font=font, fill=fill)
    combined = Image.alpha_composite(base, overlay); return combined.convert("RGB")
def add_image_watermark(img: Image.Image, wm_path: str, position: str, opacity: float, scale: float):
    if not wm_path or not os.path.exists(wm_path): return img
    try: wm = Image.open(wm_path).convert("RGBA")
    except Exception as e: raise RuntimeError(f"Watermark image load failed: {e}")
    base = img.convert("RGBA"); target_w = max(1, int(base.width * float(scale))); ratio = target_w / wm.width; target_h = int(wm.height * ratio)
    wm_resized = wm.resize((target_w, target_h), Image.LANCZOS)
    if float(opacity) < 1.0:
        alpha = wm_resized.split()[3]; alpha = ImageEnhance.Brightness(alpha).enhance(float(opacity)); wm_resized.putalpha(alpha)
    margin = max(10, int(min(base.size) * 0.02))
    pos_map = {"bottom-right": (base.width - wm_resized.width - margin, base.height - wm_resized.height - margin),"bottom-left": (margin, base.height - wm_resized.height - margin),"top-left": (margin, margin),"top-right": (base.width - wm_resized.width - margin, margin),"center": ((base.width - wm_resized.width) // 2, (base.height - wm_resized.height) // 2),}
    pos = pos_map.get(position, pos_map["bottom-right"])
    layer = Image.new("RGBA", base.size, (255, 255, 255, 0)); layer.paste(wm_resized, pos, wm_resized)
    combined = Image.alpha_composite(base, layer); return combined.convert("RGB")
def make_pdf_from_images(pil_image_paths, out_pdf_path):
    c = canvas.Canvas(str(out_pdf_path), pagesize=A4); page_w, page_h = A4
    for img_path in pil_image_paths:
        try:
            with Image.open(img_path) as im:
                im = im.convert("RGB"); img_w_px, img_h_px = im.size; ratio = min(page_w / img_w_px, page_h / img_h_px)
                draw_w = img_w_px * ratio; draw_h = img_h_px * ratio; x = (page_w - draw_w) / 2; y = (page_h - draw_h) / 2
                b = io.BytesIO(); im.save(b, format="PNG"); b.seek(0); ir = ImageReader(b)
                c.drawImage(ir, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, anchor='c'); c.showPage()
        except Exception as e: print("make_pdf_from_images: failed to add", img_path, e)
    c.save()

# --- প্রধান রুট (Main Routes) ---
@app.route('/')
def home():
    return render_template('home.html', stripe_key=app.config['STRIPE_PUBLISHABLE_KEY'])
@app.route('/tools')
def tools():
    return render_template('tools.html')

# --- স্ট্যাটিক পেইজ রুট (আগের মতোই) ---
@app.route('/features')
def features(): return render_template('features.html')
@app.route('/about')
def about_us(): return render_template('about.html')
@app.route('/contact')
def contact(): return render_template('contact.html')
@app.route('/privacy')
def privacy_policy(): return render_template('privacy.html')
@app.route('/terms')
def terms_of_service(): return render_template('terms.html')

# --- Auth Routes (আগের মতোই) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('tools'))
    form = RegistrationForm()
    if form.validate_on_submit():
        try:
            customer = stripe.Customer.create(email=form.email.data, name=form.username.data)
            user = User(username=form.username.data, email=form.email.data, password=form.password.data, stripe_customer_id=customer.id)
            db.session.add(user); db.session.commit()
            flash('Your account has been created! You are now able to log in', 'success')
            return redirect(url_for('login'))
        except stripe.error.StripeError as e: flash(f'Error creating Stripe customer: {e}', 'danger'); return render_template('register.html', title='Register', form=form)
        except Exception as e: flash(f'An error occurred: {e}', 'danger'); return render_template('register.html', title='Register', form=form)
    return render_template('register.html', title='Register', form=form)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('tools'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.verify_password(form.password.data):
            login_user(user); flash('Login Successful!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('tools'))
        else:
            flash('Login Unsuccessful. Please check email and password', 'danger')
    return render_template('login.html', title='Login', form=form)
@app.route('/logout')
def logout():
    logout_user(); flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

# --- ড্যাশবোর্ড রুট (আপডেট করা) ---
@app.route('/dashboard')
@login_required # ড্যাশবোর্ড শুধু লগইন করা ইউজাররাই দেখবে
def dashboard():
    # --- নতুন সংযোজন: ইউজারের ফাইলগুলো ডাটাবেস থেকে লোড করুন ---
    files = UserFile.query.filter_by(user_id=current_user.id).order_by(UserFile.created_at.desc()).limit(10).all()
    return render_template('dashboard.html', title='Dashboard', files=files)

# --- নতুন সংযোজন: ফাইল ডাউনলোড রুট ---
@app.route('/download_file/<filename>')
@login_required
def download_file(filename):
    # ফাইলটি ডাটাবেসে আছে কিনা এবং সেটি এই ইউজারের কিনা তা চেক করুন
    file_record = UserFile.query.filter_by(saved_filename=filename, user_id=current_user.id).first_or_404()
    
    # ইউজারকে ফাইলটি ডাউনলোড করতে দিন
    return send_from_directory(
        app.config['USER_FILES_FOLDER'],
        filename,
        as_attachment=True,
        download_name=file_record.original_filename # আসল নাম দিয়ে ডাউনলোড হবে
    )

# --- Stripe পেমেন্ট রুট (আগের মতোই) ---
@app.route('/create-checkout-session', methods=['POST'])
@login_required 
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id, payment_method_types=['card'],
            line_items=[{'price': app.config['STRIPE_PRICE_ID'], 'quantity': 1,}],
            mode='subscription', allow_promotion_codes=True,
            success_url=url_for('success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('cancel', _external=True),
        )
        return jsonify({'id': checkout_session.id})
    except Exception as e: return jsonify(error=str(e)), 403

@app.route('/success')
def success():
    flash('Your subscription was successful!', 'success')
    return render_template('success.html')
@app.route('/cancel')
def cancel():
    flash('Your subscription process was cancelled.', 'info')
    return render_template('cancel.html')

@app.route('/create-portal-session', methods=['POST'])
@login_required 
def create_portal_session():
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id, return_url=url_for('dashboard', _external=True),
        )
        return jsonify({'url': portal_session.url})
    except Exception as e: return jsonify(error=str(e)), 403

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True); sig_header = request.headers.get('Stripe-Signature')
    try: event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except ValueError as e: return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e: return 'Invalid signature', 400
    event_type = event['type']; data = event['data']['object']
    if event_type == 'checkout.session.completed':
        customer_id = data.get('customer'); user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user: user.subscription_status = 'active'; db.session.commit(); print(f"User {user.email} is now ACTIVE.")
    elif event_type == 'customer.subscription.deleted':
        customer_id = data.get('customer'); user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user: user.subscription_status = 'inactive'; db.session.commit(); print(f"User {user.email} is now INACTIVE.")
    elif event_type == 'customer.subscription.updated':
        customer_id = data.get('customer'); user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user: user.subscription_status = data.get('status'); db.session.commit(); print(f"User {user.email} status updated to {data.get('status')}.")
    else: print(f"Unhandled Stripe event type: {event_type}")
    return 'OK', 200

# --- টুলসের রুট (এখন লগইন ছাড়াও চলবে) ---

@app.route('/qr-generator', methods=['GET', 'POST'])
def qr_generator():
    qr_image_path = None
    if request.method == 'POST':
        url = request.form['url']
        if url:
            img = qrcode.make(url); qr_filename = 'qr_code_generated.png'
            qr_image_path_full = os.path.join(STATIC_FOLDER, qr_filename); img.save(qr_image_path_full)
            qr_image_path = qr_filename
    return render_template('qr_generator.html', qr_image_path=qr_image_path)
    
@app.route('/calculator')
def calculator(): return render_template('calculator.html')

@app.route('/slug-generator')
def slug_generator(): return render_template('slug_generator.html')

@app.route('/generate-slug', methods=['POST'])
def generate_slug():
    data = request.json; text = data.get('text', ''); separator = data.get('separator', '-')
    remove_numbers = data.get('remove_numbers', False); lowercase = data.get('lowercase', True) 
    final_slug = slugify(text, separator=separator, lowercase=lowercase)
    if remove_numbers:
        final_slug = "".join(c for c in final_slug if not c.isdigit()); final_slug = final_slug.replace(separator * 2, separator)
    return jsonify({'slug': final_slug})

@app.route('/list-randomizer')
def list_randomizer(): return render_template('list_randomizer.html')

@app.route('/file-converter')
def file_converter(): return render_template('file_converter.html')

@app.route('/convert', methods=['POST'])
def handle_conversion():
    files = request.files.getlist('file')
    if not files or files[0].filename == '':
        flash("No selected file", "danger"); return redirect(url_for('file_converter'))
    conversion_type = request.form['conversion_type']
    
    output_buffer = None
    download_name = "download"
    
    if conversion_type == 'jpg_to_pdf':
        try:
            output_buffer = convert_jpg_to_pdf(files)
            download_name = "converted.pdf"
            if not output_buffer:
                flash("No valid JPG images found", "danger"); return redirect(url_for('file_converter'))
        except Exception as e:
            flash(f"Error during JPG to PDF conversion: {e}", "danger"); return redirect(url_for('file_converter'))
    elif conversion_type == 'pdf_to_jpg':
        try:
            if len(files) > 1:
                flash("Please upload only one PDF for PDF-to-JPG conversion.", "danger"); return redirect(url_for('file_converter'))
            output_buffer = convert_pdf_to_jpgs(files[0])
            download_name = "converted_images.zip"
            if not output_buffer:
                flash("Error during PDF to JPG conversion. Did you install Poppler?", "danger"); return redirect(url_for('file_converter'))
        except Exception as e:
            flash(f"Error during PDF to JPG conversion: {e}. (Did you install Poppler?)", "danger"); return redirect(url_for('file_converter'))
    else:
        flash("Invalid conversion type", "danger"); return redirect(url_for('file_converter'))

    # --- নতুন সংযোজন: ফাইল সেভ করুন ---
    save_user_file(current_user, output_buffer, download_name, "File Converter")

    return send_file(
        output_buffer,
        as_attachment=True,
        download_name=download_name,
        mimetype='application/octet-stream' # জেনেরিক mimetype
    )

@app.route("/image-studio")
def image_studio(): return render_template("image_studio.html")

@app.route("/process", methods=["POST"])
def process_images():
    files = request.files.getlist("images")
    if not files or not files[0].filename:
        flash("Please select at least one image.", "error"); return redirect(url_for("image_studio"))
    try:
        resize_w = int(request.form.get("width") or 0); resize_h = int(request.form.get("height") or 0)
        keep_aspect = request.form.get("keep_aspect") == "on"; watermark_text = (request.form.get("watermark_text") or "").strip()
        wm_position = request.form.get("wm_position") or "bottom-right"
        text_opacity = float(request.form.get("text_opacity") or 0.5); img_opacity = float(request.form.get("img_opacity") or 0.5)
        text_size = int(request.form.get("text_size") or 24); image_scale = float(request.form.get("image_scale") or 0.2)
        output_format = (request.form.get("output_format") or "JPEG").upper(); quality = int(request.form.get("quality") or 90)
    except Exception as e:
        flash(f"Invalid form data: {e}", "error"); return redirect(url_for("image_studio"))
    if output_format not in FORMAT_MAP: output_format = "JPEG"
    quality = max(1, min(100, quality)); wm_path = None
    wm_file = request.files.get("watermark_image")
    if wm_file and wm_file.filename and allowed_file(wm_file.filename):
        wm_name = secure_filename(wm_file.filename)
        wm_path = os.path.join(app.config['UPLOAD_FOLDER'], f"wm_{uuid.uuid4().hex}_{wm_name}")
        try: wm_file.save(wm_path)
        except Exception as e: print("Watermark save failed:", e); wm_path = None
    processed_paths = []; errors = []
    for f in files:
        if not f or not f.filename or not allowed_file(f.filename):
            errors.append(f"{f.filename or 'Unknown file'}: unsupported type"); continue
        safe = secure_filename(f.filename); in_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}_{safe}")
        try:
            f.save(in_path)
            with Image.open(in_path) as im:
                im = im.convert("RGBA")
                if resize_w or resize_h:
                    if keep_aspect: target = (resize_w or im.width, resize_h or im.height); im.thumbnail(target, Image.LANCZOS)
                    else: new_w = resize_w or im.width; new_h = resize_h or im.height; im = im.resize((new_w, new_h), Image.LANCZOS)
                if wm_path: im = add_image_watermark(im, str(wm_path), wm_position, img_opacity, image_scale)
                if watermark_text: im = add_text_watermark(im, watermark_text, wm_position, text_opacity, text_size)
                ext = FORMAT_MAP.get(output_format, "jpg"); out_file_name = f"{uuid.uuid4().hex}_out.{ext}"
                out_path = os.path.join(app.config['PROCESSED_FOLDER'], out_file_name)
                if output_format == "PDF":
                    out_temp = os.path.join(app.config['PROCESSED_FOLDER'], f"{uuid.uuid4().hex}_pdfimg.png")
                    im.convert("RGB").save(out_temp, "PNG"); processed_paths.append(out_temp)
                elif output_format == "JPEG": im.convert("RGB").save(out_path, "JPEG", quality=quality); processed_paths.append(out_path)
                else: im.save(out_path, output_format); processed_paths.append(out_path)
        except Exception as e: errors.append(f"{safe}: processing failed ({e})")
        finally:
            if os.path.exists(in_path):
                try: os.remove(in_path)
                except Exception as e: print(f"Failed to remove temp file {in_path}: {e}")
    if not processed_paths:
        flash("No images were processed. " + ("; ".join(errors[:5]) if errors else ""), "error")
        if wm_path and os.path.exists(wm_path): os.remove(wm_path)
        return redirect(url_for("image_studio"))
    
    output_path = None
    download_name = "download"
    mimetype = "application/octet-stream"

    if output_format == "PDF":
        pdf_file = os.path.join(app.config['PROCESSED_FOLDER'], f"batch_{uuid.uuid4().hex}.pdf")
        try:
            make_pdf_from_images(processed_paths, pdf_file)
            download_name = "MyGizmo_Converted.pdf"; mimetype = "application/pdf"; output_path = pdf_file
        except Exception as e:
            flash(f"PDF generation failed: {e}", "error"); return redirect(url_for("image_studio"))
        finally:
            for p in processed_paths:
                if os.path.exists(p): os.remove(p)
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in processed_paths: zf.write(p, arcname=os.path.basename(p))
        zip_buffer.seek(0)
        for p in processed_paths:
            if os.path.exists(p): os.remove(p)
        download_name = f"MyGizmo_Images_{uuid.uuid4().hex}.zip"; mimetype = "application/zip"; output_path = zip_buffer
    
    if wm_path and os.path.exists(wm_path): os.remove(wm_path)
    
    # --- নতুন সংযোজন: ফাইল সেভ করুন ---
    save_user_file(current_user, output_path, download_name, "Image Studio")
    
    return send_file(output_path, as_attachment=True, download_name=download_name, mimetype=mimetype)


@app.route('/ai-background-remover', methods=['GET', 'POST'])
def ai_background_remover():
    if request.method == 'POST':
        file = request.files.get('image_file')
        if not file or not file.filename:
            flash('No file selected. Please upload an image.', 'error')
            return redirect(url_for('ai_background_remover'))
        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload a JPG, PNG, or WEBP image.', 'error')
            return redirect(url_for('ai_background_remover'))
        try:
            input_bytes = file.read()
            output_bytes = remove(input_bytes)
            output_buffer = io.BytesIO(output_bytes)
            output_buffer.seek(0)
            
            download_name = f'bg_removed_{file.filename}.png'
            
            # --- নতুন সংযোজন: ফাইল সেভ করুন ---
            save_user_file(current_user, output_buffer, download_name, "AI Background Remover")
            
            return send_file(
                output_buffer,
                as_attachment=True,
                download_name=download_name,
                mimetype='image/png'
            )
        except Exception as e:
            flash(f'Error during background removal: {e}', 'danger')
            return redirect(url_for('ai_background_remover'))
    return render_template('ai_background_remover.html')

# --- অ্যাপ রান করুন ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)