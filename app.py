# app.py - الملف الرئيسي المتكامل بجميع الميزات
import os
import re
import pytesseract
from PIL import Image
import PyPDF2
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
import requests
import json
from database import db, User, Conversation, Message

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'messi-super-secret-key-2025')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB حد أقصى
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# التأكد من وجود مجلد الرفع
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# تهيئة قاعدة البيانات
db.init_app(app)

# تهيئة نظام تسجيل الدخول
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# تهيئة محدد السرعة (Rate Limiting)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# إعدادات Ollama
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "deepseek-r1:7b"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# إنشاء قاعدة البيانات والمستخدم الإداري
@app.before_first_request
def create_tables():
    db.create_all()
    # إنشاء مستخدم إداري افتراضي إذا لم يكن موجوداً
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@localhost',
            is_admin=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

# استخراج النص من الملفات المرفوعة
def extract_text_from_file(file_path, file_type):
    text = ""
    try:
        if file_type.startswith('image/'):
            # استخراج النص من الصور
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image, lang='ara+eng')
        
        elif file_type == 'application/pdf':
            # استخراج النص من PDF
            with open(file_path, 'rb') as f:
                pdf_reader = PyPDF2.PdfReader(f)
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
        
        elif file_type.startswith('text/'):
            # قراءة الملفات النصية
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
    
    except Exception as e:
        print(f"خطأ في استخراج النص: {e}")
        text = f"[حدث خطأ في قراءة الملف: {str(e)}]"
    
    return text

# الصفحة الرئيسية
@app.route('/')
def index():
    if current_user.is_authenticated:
        # جلب المحادثات السابقة للمستخدم
        conversations = Conversation.query.filter_by(user_id=current_user.id)\
                          .order_by(Conversation.updated_at.desc()).all()
        return render_template('index.html', conversations=conversations)
    return redirect(url_for('login'))

# تسجيل الدخول
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user, remember=True)
            # إعادة تعيين العداد اليومي إذا لزم الأمر
            session.permanent = True
            return redirect(url_for('index'))
        
        flash('اسم المستخدم أو كلمة المرور غير صحيحة', 'error')
    
    return render_template('login.html')

# تسجيل مستخدم جديد
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # التحقق من عدم وجود المستخدم مسبقاً
        if User.query.filter_by(username=username).first():
            flash('اسم المستخدم موجود بالفعل', 'error')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('البريد الإلكتروني مستخدم بالفعل', 'error')
            return redirect(url_for('register'))
        
        # إنشاء المستخدم الجديد
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('تم إنشاء الحساب بنجاح! يمكنك تسجيل الدخول الآن', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

# تسجيل الخروج
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# صفحة الملف الشخصي
@app.route('/profile')
@login_required
def profile():
    conversations_count = Conversation.query.filter_by(user_id=current_user.id).count()
    messages_count = Message.query.join(Conversation).filter(Conversation.user_id == current_user.id).count()
    
    return render_template('profile.html', 
                         conversations_count=conversations_count,
                         messages_count=messages_count)

# إنشاء محادثة جديدة
@app.route('/api/new_conversation', methods=['POST'])
@login_required
def new_conversation():
    conversation = Conversation(user_id=current_user.id)
    db.session.add(conversation)
    db.session.commit()
    
    return jsonify({
        'id': conversation.id,
        'title': conversation.title,
        'created_at': conversation.created_at.isoformat()
    })

# إرسال سؤال مع دعم المرفقات
@app.route('/api/ask', methods=['POST'])
@login_required
@limiter.limit("10 per day")
def ask():
    # التحقق من العداد اليومي
    if current_user.daily_questions <= 0:
        return jsonify({'error': 'لقد استنفدت أسئلتك اليومية. عد غداً!'}), 403
    
    # استقبال البيانات
    conversation_id = request.form.get('conversation_id', type=int)
    user_prompt = request.form.get('prompt', '')
    file = request.files.get('file')
    
    if not user_prompt and not file:
        return jsonify({'error': 'الرجاء إدخال سؤال أو رفع ملف'}), 400
    
    # معالجة الملف المرفوع
    file_text = ""
    file_name = None
    file_type = None
    
    if file:
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        file_type = file.content_type
        file_name = filename
        
        # استخراج النص من الملف
        file_text = extract_text_from_file(file_path, file_type)
        
        # دمج النص المستخرج مع السؤال
        if file_text:
            user_prompt = f"{user_prompt}\n\n[محتوى الملف المرفق ({filename})]:\n{file_text[:2000]}"
    
    # الحصول على أو إنشاء المحادثة
    if not conversation_id:
        conversation = Conversation(user_id=current_user.id)
        db.session.add(conversation)
        db.session.flush()
        conversation_id = conversation.id
    else:
        conversation = Conversation.query.get_or_404(conversation_id)
        if conversation.user_id != current_user.id:
            return jsonify({'error': 'غير مصرح'}), 403
    
    # حفظ رسالة المستخدم
    user_message = Message(
        conversation_id=conversation_id,
        role='user',
        content=user_prompt,
        file_name=file_name,
        file_type=file_type
    )
    db.session.add(user_message)
    
    # تجهيز سياق المحادثة (آخر 10 رسائل)
    previous_messages = Message.query.filter_by(conversation_id=conversation_id)\
                           .order_by(Message.created_at.desc())\
                           .limit(10).all()
    previous_messages.reverse()
    
    messages_for_ai = []
    for msg in previous_messages:
        messages_for_ai.append({
            'role': 'user' if msg.role == 'user' else 'assistant',
            'content': msg.content
        })
    
    # إضافة الرسالة الحالية
    messages_for_ai.append({'role': 'user', 'content': user_prompt})
    
    # إرسال الطلب إلى Ollama
    try:
        payload = {
            "model": MODEL_NAME,
            "messages": messages_for_ai,
            "stream": False
        }
        
        response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        ai_response = result.get('message', {}).get('content', 'لم يتم استلام رد')
        
        # حفظ رد الذكاء الاصطناعي
        ai_message = Message(
            conversation_id=conversation_id,
            role='assistant',
            content=ai_response
        )
        db.session.add(ai_message)
        
        # تحديث إحصائيات المستخدم
        current_user.daily_questions -= 1
        current_user.total_questions += 1
        
        # تحديث عنوان المحادثة إذا كانت جديدة
        if len(previous_messages) <= 1:
            # استخدام أول 30 حرف من السؤال كعنوان
            conversation.title = user_prompt[:30] + ('...' if len(user_prompt) > 30 else '')
        
        db.session.commit()
        
        return jsonify({
            'response': ai_response,
            'remaining': current_user.daily_questions,
            'conversation_id': conversation_id
        })
        
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'لا يمكن الاتصال بـ Ollama. تأكد من تشغيله!'}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'حدث خطأ: {str(e)}'}), 500

# جلب رسائل محادثة معينة
@app.route('/api/conversation/<int:conversation_id>')
@login_required
def get_conversation(conversation_id):
    conversation = Conversation.query.get_or_404(conversation_id)
    
    if conversation.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'غير مصرح'}), 403
    
    messages = Message.query.filter_by(conversation_id=conversation_id)\
                .order_by(Message.created_at).all()
    
    return jsonify({
        'id': conversation.id,
        'title': conversation.title,
        'messages': [{
            'role': m.role,
            'content': m.content,
            'file_name': m.file_name,
            'created_at': m.created_at.isoformat()
        } for m in messages]
    })

# لوحة تحكم المطور (Admin)
@app.route('/admin')
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash('غير مصرح بالوصول', 'error')
        return redirect(url_for('index'))
    
    users_count = User.query.count()
    conversations_count = Conversation.query.count()
    messages_count = Message.query.count()
    
    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    
    return render_template('admin.html',
                         users_count=users_count,
                         conversations_count=conversations_count,
                         messages_count=messages_count,
                         recent_users=recent_users)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)