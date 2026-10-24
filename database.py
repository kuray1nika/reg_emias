import sqlite3
from datetime import datetime, timedelta

DB_NAME = 'bd.db'


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def parse_date(date_str):
    try:
        return datetime.strptime(date_str.strip(), '%d.%m.%Y')
    except (AttributeError, ValueError):
        return None


def parse_dt(date_str, time_str=None):
    try:
        if time_str is None:
            return datetime.strptime(date_str.strip(), '%d.%m.%Y %H:%M')
        return datetime.strptime(f"{date_str.strip()} {time_str.strip()}", '%d.%m.%Y %H:%M')
    except (AttributeError, ValueError):
        return None


def _split_items(text):
    return [t.strip() for t in (text or '').split(';') if t.strip()]


def ensure_schema():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('PRAGMA table_info(appointment)')
        cols = {row[1] for row in cursor.fetchall()}
        if 'record_id' not in cols:
            cursor.execute('ALTER TABLE appointment ADD COLUMN record_id INTEGER')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка миграции схемы: {e}")
    finally:
        conn.close()


def register_patient(last_name, first_name, middle_name, birth_date, gender,
                     oms_policy, phone, login, password):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT 1 FROM users WHERE login = ?', (login,))
        if cursor.fetchone():
            return 'Такой логин (почта) уже занят.'

        cursor.execute('SELECT 1 FROM patients WHERE oms_policy = ?', (oms_policy,))
        if cursor.fetchone():
            return 'Пациент с таким полисом ОМС уже зарегистрирован.'

        cursor.execute('SELECT MAX(pat_id) FROM patients')
        max_id = cursor.fetchone()[0]
        new_pat_id = (max_id + 1) if max_id else 150001

        cursor.execute('''
            INSERT INTO patients (pat_id, last_name, first_name, middle_name,
                                  birth_date, gender, oms_policy, phone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (new_pat_id, last_name, first_name, middle_name,
              birth_date, gender, oms_policy, phone))

        cursor.execute('''
            INSERT INTO users (login, password, pat_id)
            VALUES (?, ?, ?)
        ''', (login, password, new_pat_id))

        conn.commit()
        return None
    except Exception as e:
        conn.rollback()
        print(f"Ошибка регистрации: {e}")
        return 'Ошибка при регистрации. Попробуйте позже.'
    finally:
        conn.close()


def check_user(login, password):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE login = ? AND password = ?', (login, password))
    user = cursor.fetchone()
    conn.close()
    return user


def get_patient_by_pat_id(pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT last_name, first_name, middle_name FROM patients WHERE pat_id = ?', (pat_id,))
    patient = cursor.fetchone()
    conn.close()
    return patient


def get_doctor_by_doc_id(doc_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT last_name, first_name, middle_name FROM doctors WHERE doc_id = ?', (doc_id,))
    doctor = cursor.fetchone()
    conn.close()
    return doctor


def _gender_text(value):
    v = (value or '').strip().lower()
    return 'Женский' if v == 'ж' else ('Мужской' if v == 'м' else 'Не указан')


def get_patient_medical_data(pat_id):
    ensure_schema()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT birth_date, gender, oms_policy, phone FROM patients WHERE pat_id = ?', (pat_id,))
    p_info = cursor.fetchone()

    cursor.execute('SELECT anamnesis, documents FROM med_card WHERE pat_id = ?', (pat_id,))
    mc_info = cursor.fetchone()

    # История: все записи приёма + дата из records (если есть связь)
    cursor.execute('''
        SELECT a.ID, a.complaints, a.medication, a.directions, a.recommendations,
               a.record_id, r.time_appointment
        FROM appointment a
        LEFT JOIN records r ON r.ID = a.record_id
        WHERE a.pat_id = ?
        ORDER BY COALESCE(r.time_appointment, a.ID) DESC
    ''', (pat_id,))
    appts = cursor.fetchall()
    conn.close()

    return {
        'birth_date': (p_info['birth_date'] if p_info else None) or 'Не указано',
        'gender': _gender_text(p_info['gender'] if p_info else None),
        'oms_policy': (p_info['oms_policy'] if p_info else None) or 'Не указано',
        'phone': (p_info['phone'] if p_info else None) or 'Не указан',
        'anamnesis': (mc_info['anamnesis'] if mc_info else None) or 'Не заполнено',
        'documents': (mc_info['documents'] if mc_info else None) or 'Не заполнено',
        'appointments': appts,
    }


def get_patient_directions(pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT directions FROM appointment WHERE pat_id = ? ORDER BY ID DESC', (pat_id,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    seen = set()
    for r in rows:
        for item in _split_items(r['directions']):
            if item.lower().startswith('[использовано]'):
                continue
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def get_patient_prescriptions(pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT medication FROM appointment WHERE pat_id = ? ORDER BY ID DESC', (pat_id,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.extend(_split_items(r['medication']))
    return result


def mark_direction_used(pat_id, specialty):
    if not specialty:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT ID, directions FROM appointment WHERE pat_id = ? ORDER BY ID DESC',
            (pat_id,)
        )
        rows = cursor.fetchall()
        spec_l = specialty.strip().lower()
        for row in rows:
            items = _split_items(row['directions'])
            changed = False
            new_items = []
            for item in items:
                if item.lower().startswith('[использовано]'):
                    new_items.append(item)
                    continue
                if spec_l in item.lower():
                    new_items.append(f'[использовано] {item}')
                    changed = True
                else:
                    new_items.append(item)
            if changed:
                cursor.execute(
                    'UPDATE appointment SET directions = ? WHERE ID = ?',
                    ('; '.join(new_items), row['ID'])
                )
                conn.commit()
                return True
        return False
    except Exception as e:
        conn.rollback()
        print(f"Ошибка закрытия направления: {e}")
        return False
    finally:
        conn.close()


def get_med_centers():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT ID, name, street, house, extra_info, phone FROM med_center ORDER BY name')
    rows = cursor.fetchall()
    conn.close()
    return rows


_DOCTOR_SELECT = '''
    SELECT d.doc_id, d.last_name, d.first_name, d.middle_name, d.specialties,
           mc.name AS center_name, mc.street, mc.house
    FROM doctors d
    LEFT JOIN med_center mc ON d.work_place = mc.ID
'''


def get_all_specialties():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT specialties FROM doctors WHERE specialties IS NOT NULL ORDER BY specialties')
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result


def get_doctors(specialty=None):
    conn = get_connection()
    cursor = conn.cursor()
    sql = _DOCTOR_SELECT
    params = ()
    if specialty:
        sql += ' WHERE d.specialties = ?'
        params = (specialty,)
    sql += ' ORDER BY d.specialties, d.last_name'
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_doctor_card(doc_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(_DOCTOR_SELECT + ' WHERE d.doc_id = ?', (doc_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def get_free_slots(doc_id, date=None, only_future=True):
    conn = get_connection()
    cursor = conn.cursor()
    if date:
        cursor.execute('SELECT data, time, office FROM free_time WHERE doc_id = ? AND data = ?', (doc_id, date))
    else:
        cursor.execute('SELECT data, time, office FROM free_time WHERE doc_id = ?', (doc_id,))
    rows = cursor.fetchall()
    conn.close()

    now = datetime.now()
    slots = []
    for r in rows:
        dt = parse_dt(r['data'], r['time'])
        if dt is None or (only_future and dt < now):
            continue
        slots.append({'date': r['data'], 'time': r['time'], 'label': dt.strftime('%H:%M'),
                      'office': r['office'], 'dt': dt})
    slots.sort(key=lambda s: s['dt'])
    return slots


DEFAULT_DURATION = 20
BREAK_MINUTES = 5


def get_appointment_duration(cursor, doc_id):
    cursor.execute('''
        SELECT schedule_priem FROM directions
        WHERE doc_id = ? AND schedule_priem GLOB '[0-9]*'
        ORDER BY ID LIMIT 1
    ''', (doc_id,))
    row = cursor.fetchone()
    try:
        minutes = int(row['schedule_priem']) if row else DEFAULT_DURATION
    except (TypeError, ValueError):
        minutes = DEFAULT_DURATION
    return minutes if minutes > 0 else DEFAULT_DURATION


def generate_free_slots(doc_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    added = 0
    try:
        cursor.execute('BEGIN IMMEDIATE')

        sql = 'SELECT doc_id, date, start_time, end_time, office FROM schedule_doctors'
        params = ()
        if doc_id is not None:
            sql += ' WHERE doc_id = ?'
            params = (doc_id,)
        cursor.execute(sql, params)
        schedule = cursor.fetchall()
        busy = {}
        cursor.execute('SELECT doc_id, data, time FROM free_time')
        for r in cursor.fetchall():
            dt = parse_dt(r['data'], r['time'])
            if dt:
                busy.setdefault(r['doc_id'], []).append(dt)
        cursor.execute('SELECT doc_id, time_appointment FROM records')
        for r in cursor.fetchall():
            dt = parse_dt(r['time_appointment'])
            if dt:
                busy.setdefault(r['doc_id'], []).append(dt)

        now = datetime.now()
        durations = {}
        for row in schedule:
            d_id = row['doc_id']
            if d_id not in durations:
                durations[d_id] = get_appointment_duration(cursor, d_id)
            duration = timedelta(minutes=durations[d_id])
            step = duration + timedelta(minutes=BREAK_MINUTES)

            start = parse_dt(str(row['date']), row['start_time'])
            end = parse_dt(str(row['date']), row['end_time'])
            if not start or not end:
                continue

            taken = busy.setdefault(d_id, [])
            current = start
            while current + duration <= end:
                overlaps = any(current < t + step and t < current + step for t in taken)
                if current > now and not overlaps:
                    cursor.execute(
                        'INSERT INTO free_time (data, time, doc_id, office) VALUES (?, ?, ?, ?)',
                        (current.strftime('%d.%m.%Y'), f"{current.hour}:{current.minute:02d}",
                         d_id, row['office']))
                    taken.append(current)
                    added += 1
                current += step
        conn.commit()
        return added
    except Exception as e:
        conn.rollback()
        print(f"Ошибка генерации окон: {e}")
        return 0
    finally:
        conn.close()


def get_free_dates(doc_id):
    dates = []
    for s in get_free_slots(doc_id):
        if s['date'] not in dates:
            dates.append(s['date'])
    return dates


def book_slot(pat_id, doc_id, date, time, specialty=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT ID, office FROM free_time WHERE doc_id = ? AND data = ? AND time = ?',
                       (doc_id, date, time))
        slot = cursor.fetchone()
        if not slot:
            return None

        cursor.execute('SELECT work_place, specialties FROM doctors WHERE doc_id = ?', (doc_id,))
        doc = cursor.fetchone()
        if not doc:
            return None

        cursor.execute('DELETE FROM free_time WHERE ID = ?', (slot['ID'],))
        if cursor.rowcount != 1:
            conn.rollback()
            return None

        cursor.execute('''
            INSERT INTO records (pat_id, doc_id, time_appointment, office, med_center)
            VALUES (?, ?, ?, ?, ?)
        ''', (pat_id, doc_id, f"{date} {time}", slot['office'], doc['work_place']))
        record_id = cursor.lastrowid
        conn.commit()

        # закрываем направление после успешной записи
        spec = specialty or (doc['specialties'] if doc else None)
        if spec:
            mark_direction_used(pat_id, spec)

        return record_id
    except Exception as e:
        conn.rollback()
        print(f"Ошибка записи на приём: {e}")
        return None
    finally:
        conn.close()


_RECORD_SELECT = '''
    SELECT r.ID AS record_id, r.pat_id, r.doc_id, r.time_appointment, r.office,
           COALESCE(r.status, 'scheduled') AS status,
           d.last_name, d.first_name, d.middle_name, d.specialties,
           mc.name AS center_name, mc.street, mc.house
    FROM records r
    LEFT JOIN doctors d ON d.doc_id = r.doc_id
    LEFT JOIN med_center mc ON mc.ID = COALESCE(NULLIF(r.med_center, ''), d.work_place)
'''


def get_patient_records(pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(_RECORD_SELECT + ' WHERE r.pat_id = ?', (pat_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_record(record_id, pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(_RECORD_SELECT + ' WHERE r.ID = ? AND r.pat_id = ?', (record_id, pat_id))
    row = cursor.fetchone()
    conn.close()
    return row


def get_doctor_records(doc_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.ID AS record_id, r.pat_id, r.time_appointment, r.office,
               COALESCE(r.status, 'scheduled') AS status,
               p.last_name, p.first_name, p.middle_name, p.birth_date
        FROM records r
        JOIN patients p ON p.pat_id = r.pat_id
        WHERE r.doc_id = ?
    ''', (doc_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_patient_for_doctor(doc_id, pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.pat_id, p.last_name, p.first_name, p.middle_name,
               p.birth_date, p.gender, p.oms_policy, p.phone
        FROM patients p
        WHERE p.pat_id = ?
          AND EXISTS (SELECT 1 FROM records r WHERE r.pat_id = p.pat_id AND r.doc_id = ?)
    ''', (pat_id, doc_id))
    row = cursor.fetchone()
    conn.close()
    return row


def get_appointment_for_record(pat_id, record_id):
    ensure_schema()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ID, complaints, medication, directions, recommendations, record_id
        FROM appointment
        WHERE pat_id = ? AND record_id = ?
        LIMIT 1
    ''', (pat_id, record_id))
    row = cursor.fetchone()
    conn.close()
    return row


def get_latest_appointment(pat_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ID, complaints, medication, directions, recommendations
        FROM appointment WHERE pat_id = ? ORDER BY ID DESC LIMIT 1
    ''', (pat_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def get_direction_catalog():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT specialties FROM doctors WHERE specialties IS NOT NULL ORDER BY specialties')
    result = [r[0] for r in cursor.fetchall()]
    conn.close()
    return result


def _ensure_appointment_for_record(cursor, pat_id, record_id):
    ensure_schema()
    cursor.execute(
        'SELECT ID FROM appointment WHERE pat_id = ? AND record_id = ?',
        (pat_id, record_id)
    )
    row = cursor.fetchone()
    if row:
        return row['ID']
    cursor.execute(
        'INSERT INTO appointment (pat_id, record_id) VALUES (?, ?)',
        (pat_id, record_id)
    )
    return cursor.lastrowid


def update_appointment(pat_id, record_id, complaints, recommendations):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        appt_id = _ensure_appointment_for_record(cursor, pat_id, record_id)
        cursor.execute(
            'UPDATE appointment SET complaints = ?, recommendations = ? WHERE ID = ?',
            (complaints, recommendations, appt_id)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Ошибка сохранения приёма: {e}")
        return False
    finally:
        conn.close()


def append_to_appointment(pat_id, record_id, field, text):
    if field not in ('directions', 'medication'):
        raise ValueError('bad field')
    conn = get_connection()
    cursor = conn.cursor()
    try:
        appt_id = _ensure_appointment_for_record(cursor, pat_id, record_id)
        cursor.execute(f'SELECT {field} FROM appointment WHERE ID = ?', (appt_id,))
        current = (cursor.fetchone()[0] or '').strip()
        new_value = f"{current}; {text}" if current else text
        cursor.execute(f'UPDATE appointment SET {field} = ? WHERE ID = ?', (new_value, appt_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Ошибка записи назначения: {e}")
        return False
    finally:
        conn.close()


def get_doctor_record(doc_id, pat_id, record_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ID AS record_id, time_appointment, COALESCE(status, 'scheduled') AS status
        FROM records
        WHERE ID = ? AND doc_id = ? AND pat_id = ?
    ''', (record_id, doc_id, pat_id))
    row = cursor.fetchone()
    conn.close()
    return row


def complete_record(doc_id, pat_id, record_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            UPDATE records SET status = 'completed'
            WHERE ID = ? AND doc_id = ? AND pat_id = ?
              AND COALESCE(status, 'scheduled') = 'scheduled'
        ''', (record_id, doc_id, pat_id))
        conn.commit()
        return cursor.rowcount == 1
    except Exception as e:
        conn.rollback()
        print(f"Ошибка завершения приёма: {e}")
        return False
    finally:
        conn.close()
