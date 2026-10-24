from flask import Flask, render_template, request, redirect, url_for, session
from datetime import datetime, date, timedelta
import re
import database
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
          'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

COMPLETED_VISIBLE_DAYS = 30


def date_label(date_str, with_weekday=True):
    d = database.parse_date(date_str)
    if not d:
        return date_str or ''
    text = f"{d.day} {MONTHS[d.month - 1]}"
    return f"{WEEKDAYS[d.weekday()]}, {text}" if with_weekday else text


def age_text(birth_date):
    d = database.parse_date(birth_date)
    if not d:
        return None
    today = date.today()
    n = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    if n % 10 == 1 and n % 100 != 11:
        word = 'год'
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = 'года'
    else:
        word = 'лет'
    return f"{n} {word}"


def full_name(row, order=('last_name', 'first_name')):
    return ' '.join(row[k] for k in order if row[k])


def address(row):
    parts = []
    if row['street']:
        parts.append(row['street'])
    if row['house']:
        parts.append(str(row['house']))
    return ', '.join(parts)


def match_specialty(text, specialties):
    t = text.lower()
    for s in specialties:
        if s and s.lower() in t:
            return s
    return None


def record_view(r, dt):
    return {
        'record_id': r['record_id'],
        'doctor_name': full_name(r),
        'specialty': r['specialties'] or '',
        'center': r['center_name'] or '',
        'address': address(r),
        'office': r['office'],
        'date_label': date_label(dt.strftime('%d.%m.%Y')),
        'date_short': date_label(dt.strftime('%d.%m.%Y'), with_weekday=False),
        'time': dt.strftime('%H:%M'),
        'dt': dt,
    }


def render_patient(screen, **extra):
    pat_id = session['pat_id']
    now = datetime.now()
    specialties = database.get_all_specialties()

    upcoming, past = [], []
    for r in database.get_patient_records(pat_id):
        dt = database.parse_dt(r['time_appointment'])
        if dt is None:
            continue
        if r['status'] == 'completed':
            past.append(record_view(r, dt))
        elif dt >= now:
            upcoming.append(record_view(r, dt))
    upcoming.sort(key=lambda x: x['dt'])
    past.sort(key=lambda x: x['dt'], reverse=True)

    directions = []
    for t in database.get_patient_directions(pat_id):
        spec = match_specialty(t, specialties)
        if spec:
            directions.append({'text': t, 'specialty': spec})

    ctx = dict(
        active_screen=screen,
        user_name=session.get('user_name'),
        med_data=database.get_patient_medical_data(pat_id),
        specialties=specialties,
        next_record=upcoming[0] if upcoming else None,
        last_visit=past[0] if past else None,
        directions=directions,
        prescriptions=database.get_patient_prescriptions(pat_id),
    )
    ctx.update(extra)
    return render_template('index.html', **ctx)


def render_doctor(screen, **extra):
    doc_id = session['doc_id']
    now = datetime.now()
    cutoff = now - timedelta(days=COMPLETED_VISIBLE_DAYS)

    items = []
    for r in database.get_doctor_records(doc_id):
        dt = database.parse_dt(r['time_appointment'])
        items.append({
            'record_id': r['record_id'],
            'status': r['status'],
            'pat_id': r['pat_id'],
            'name': full_name(r),
            'age': age_text(r['birth_date']),
            'dt': dt,
            'when': (f"{date_label(dt.strftime('%d.%m.%Y'), False)}, {dt.strftime('%H:%M')}"
                     if dt else 'время не указано'),
            'office': r['office'],
        })

    upcoming = [
        i for i in items
        if i['status'] == 'scheduled' and i['dt'] and i['dt'] >= now
    ]
    upcoming.sort(key=lambda x: x['dt'])

    finished = [
        i for i in items
        if i['status'] == 'completed' and i['dt'] and i['dt'] >= cutoff
    ]
    finished.sort(key=lambda x: x['dt'], reverse=True)

    ordered = upcoming + finished

    slots = database.get_free_slots(doc_id)

    nearest_slot = None
    if slots:
        s = slots[0]
        nearest_slot = f"{date_label(s['date'], False)}, {s['label']}"

    ctx = dict(
        active_screen=screen,
        user_name=session.get('user_name'),
        doc_records=ordered,
        doc_total=len(items),
        doc_finished=len([i for i in items if i['status'] == 'completed']),
        next_patient=upcoming[0] if upcoming else None,
        free_count=len(slots),
        nearest_slot=nearest_slot,
    )
    ctx.update(extra)
    return render_template('index.html', **ctx)



@app.route('/')
def index():
    if 'pat_id' in session:
        return redirect(url_for('patient_dashboard'))
    elif 'doc_id' in session:
        return redirect(url_for('doctor_dashboard'))
    return render_template('index.html', active_screen='p-login', user_name=None)


GENDERS = {'female': 'ж', 'male': 'м'}


def parse_registration(form):
    last_name = form.get('last_name', '').strip()
    first_name = form.get('first_name', '').strip()
    middle_name = form.get('middle_name', '').strip()
    login = form.get('login', '').strip()
    phone = form.get('phone', '').strip()
    password = form.get('password', '')
    oms_digits = re.sub(r'\s+', '', form.get('oms', ''))
    gender = GENDERS.get(form.get('gender'))
    try:
        birth = datetime.strptime(form.get('birth_date', ''), '%Y-%m-%d')
    except ValueError:
        birth = None

    if not (last_name and first_name and login and phone and password and oms_digits and form.get('gender')):
        return None, "Пожалуйста, заполните все поля!"
    if birth is None or birth > datetime.now():
        return None, "Укажите корректную дату рождения."
    if not re.fullmatch(r'\d{16}', oms_digits):
        return None, "Полис ОМС должен состоять из 16 цифр."
    if gender is None:
        return None, "Выберите пол."
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', login):
        return None, "Введите почту вида name@domain.ru."
    if not re.fullmatch(r'\+7\d{10}', phone):
        return None, "Телефон должен быть в формате +7XXXXXXXXXX."
    if len(password) < 8:
        return None, "Пароль должен содержать не менее 8 символов."

    return {
        'last_name': last_name,
        'first_name': first_name,
        'middle_name': middle_name,
        'birth_date': birth.strftime('%d.%m.%Y'),  # в БД дата хранится как дд.мм.гггг
        'gender': gender,
        # полис в БД хранится группами: 4509 0000 0000 0000
        'oms_policy': ' '.join(oms_digits[k:k + 4] for k in range(0, 16, 4)),
        'phone': phone,
        'login': login,
        'password': password,
    }, None


@app.route('/register', methods=['POST'])
def register():
    data, problem = parse_registration(request.form)
    if problem:
        return problem

    problem = database.register_patient(**data)
    if problem:
        return problem

    user = database.check_user(data['login'], data['password'])
    pat_id = user[4]
    patient = database.get_patient_by_pat_id(pat_id)
    session['pat_id'] = pat_id
    session['user_name'] = f"{patient[1]} {patient[0]}" if patient else data['first_name']
    return redirect(url_for('patient_dashboard'))


@app.route('/login', methods=['POST'])
def login():
    login_input = request.form.get('login')
    password = request.form.get('password')

    user = database.check_user(login_input, password)
    if user:
        pat_id = user[4]
        doc_id = user[3]

        if pat_id:
            patient = database.get_patient_by_pat_id(pat_id)
            session['pat_id'] = pat_id
            session['user_name'] = f"{patient[1]} {patient[0]}" if patient else "Пациент"
            return redirect(url_for('patient_dashboard'))
        elif doc_id:
            doctor = database.get_doctor_by_doc_id(doc_id)
            session['doc_id'] = doc_id
            session['user_name'] = f"{doctor[1]} {doctor[0]}" if doctor else "Врач"
            return redirect(url_for('doctor_dashboard'))
        return "Ошибка: не определена роль пользователя"
    return "Неверный логин или пароль"


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/patient_dashboard')
def patient_dashboard():
    if 'pat_id' not in session:
        return redirect(url_for('index'))
    return render_patient('p-home')


@app.route('/med_card')
def med_card():
    if 'pat_id' not in session:
        return redirect(url_for('index'))
    return render_patient('p-card')


@app.route('/book')
def book():
    if 'pat_id' not in session:
        return redirect(url_for('index'))
    return render_patient('p-book', doctors=database.get_doctors())


@app.route('/book/<specialty>')
def book_doctors(specialty):
    if 'pat_id' not in session:
        return redirect(url_for('index'))
    return render_patient('p-book',
                          doctors=database.get_doctors(specialty),
                          selected_specialty=specialty)


@app.route('/book_time/<int:doc_id>')
def book_time(doc_id):
    if 'pat_id' not in session:
        return redirect(url_for('index'))

    doctor = database.get_doctor_card(doc_id)
    if not doctor:
        return redirect(url_for('book'))

    database.generate_free_slots(doc_id)

    dates = database.get_free_dates(doc_id)
    selected = request.args.get('date')
    if selected not in dates:
        selected = dates[0] if dates else None

    slots = database.get_free_slots(doc_id, selected) if selected else []

    return render_patient('p-book-time',
                          doctor=doctor,
                          doctor_address=address(doctor),
                          dates=[{'value': d, 'label': date_label(d)} for d in dates],
                          selected_date=selected,
                          slots=slots)


@app.route('/book_confirm', methods=['POST'])
def book_confirm():
    if 'pat_id' not in session:
        return redirect(url_for('index'))

    doc_id = request.form.get('doc_id', type=int)
    slot_date = request.form.get('date')
    slot_time = request.form.get('time')
    specialty = request.form.get('specialty')
    if not (doc_id and slot_date and slot_time):
        return redirect(url_for('book'))

    record_id = database.book_slot(
        session['pat_id'], doc_id, slot_date, slot_time, specialty=specialty
    )
    if record_id is None:
        # окно уже заняли — возвращаем к выбору времени
        return redirect(url_for('book_time', doc_id=doc_id, date=slot_date))
    return redirect(url_for('booked', record_id=record_id))


@app.route('/booked/<int:record_id>')
def booked(record_id):
    if 'pat_id' not in session:
        return redirect(url_for('index'))
    r = database.get_record(record_id, session['pat_id'])
    dt = database.parse_dt(r['time_appointment']) if r else None
    if not dt:
        return redirect(url_for('patient_dashboard'))
    return render_patient('p-book-done', booking=record_view(r, dt))


@app.route('/doctor_dashboard')
def doctor_dashboard():
    if 'doc_id' not in session:
        return redirect(url_for('index'))
    return render_doctor('p-doc', done=request.args.get('done'))


@app.route('/doctor/patient/<int:pat_id>')
def doctor_patient(pat_id):
    if 'doc_id' not in session:
        return redirect(url_for('index'))

    patient = database.get_patient_for_doctor(session['doc_id'], pat_id)
    rec = database.get_doctor_record(session['doc_id'], pat_id, request.args.get('record', type=int))
    if not patient or not rec:
        return redirect(url_for('doctor_dashboard'))

    appt = database.get_appointment_for_record(pat_id, rec['record_id'])

    return render_doctor('p-doc-patient',
                         patient=patient,
                         rec=rec,
                         read_only=rec['status'] != 'scheduled',
                         patient_age=age_text(patient['birth_date']),
                         pat_med=database.get_patient_medical_data(pat_id),
                         appt=appt,
                         direction_catalog=database.get_direction_catalog(),
                         done=request.args.get('done'))


def _open_record_id(pat_id):
    if 'doc_id' not in session:
        return None
    record_id = request.form.get('record_id', type=int)
    rec = database.get_doctor_record(session['doc_id'], pat_id, record_id)
    return record_id if rec and rec['status'] == 'scheduled' else None


def _back(pat_id, record_id, done):
    return redirect(url_for('doctor_patient', pat_id=pat_id, record=record_id, done=done))


@app.route('/doctor/patient/<int:pat_id>/save', methods=['POST'])
def doctor_save(pat_id):
    record_id = _open_record_id(pat_id)
    if not record_id:
        return redirect(url_for('doctor_dashboard', done='locked'))
    database.update_appointment(
        pat_id, record_id,
        request.form.get('complaints', '').strip(),
        request.form.get('recommendations', '').strip()
    )
    return _back(pat_id, record_id, 'save')


@app.route('/doctor/patient/<int:pat_id>/direction', methods=['POST'])
def doctor_direction(pat_id):
    record_id = _open_record_id(pat_id)
    if not record_id:
        return redirect(url_for('doctor_dashboard', done='locked'))
    direction = request.form.get('direction', '').strip()
    if direction in database.get_direction_catalog():
        database.append_to_appointment(pat_id, record_id, 'directions', direction)
    return _back(pat_id, record_id, 'direction')


@app.route('/doctor/patient/<int:pat_id>/prescription', methods=['POST'])
def doctor_prescription(pat_id):
    record_id = _open_record_id(pat_id)
    if not record_id:
        return redirect(url_for('doctor_dashboard', done='locked'))
    drug = request.form.get('drug', '').strip().replace(';', ',')
    usage = request.form.get('usage', '').strip().replace(';', ',')
    if drug:
        database.append_to_appointment(
            pat_id, record_id, 'medication',
            f"{drug} — {usage}" if usage else drug
        )
        return _back(pat_id, record_id, 'prescription')
    return _back(pat_id, record_id, None)


@app.route('/doctor/patient/<int:pat_id>/close', methods=['POST'])
def doctor_close(pat_id):
    record_id = _open_record_id(pat_id)
    if not record_id:
        return redirect(url_for('doctor_dashboard', done='locked'))
    database.update_appointment(
        pat_id, record_id,
        request.form.get('complaints', '').strip(),
        request.form.get('recommendations', '').strip()
    )
    ok = database.complete_record(session['doc_id'], pat_id, record_id)
    return redirect(url_for('doctor_dashboard', done='closed' if ok else 'locked'))


if __name__ == '__main__':
    database.ensure_schema()
    app.run(debug=True)
