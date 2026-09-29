import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

ROLES = {"admin", "organizer", "judge", "participant"}

def get_connection():
    return sqlite3.connect("data/events.db")

def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (
                role IN ('admin', 'organizer', 'judge', 'participant')
            ),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, email, password_hash, role, is_active FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row

def get_user_by_email(email):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, email, password_hash, role, is_active FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    conn.close()
    return row

def create_user(email, password, role):
    conn = get_connection()
    conn.execute(
        "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
        (email, generate_password_hash(password), role),
    )
    conn.commit()
    conn.close()

def get_or_create_user(email, password, role):
    user = get_user_by_email(email)
    if user is None:
        create_user(email, password, role)
        user = get_user_by_email(email)
    return user

def check_password(password, password_hash):
    return check_password_hash(password_hash, password)

def create_event(event_name, event_date, event_location):
    conn = sqlite3.connect('data/events.db')
    cursor = conn.cursor()

    # Create the events table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_location TEXT NOT NULL
        )
    ''')

    # Insert the new event into the events table
    cursor.execute('''
        INSERT INTO events (event_name, event_date, event_location)
        VALUES (?, ?, ?)
    ''', (event_name, event_date, event_location))

    conn.commit()
    conn.close()

def get_events():
    conn = sqlite3.connect('data/events.db')
    cursor = conn.cursor()

    # Retrieve all events from the events table
    cursor.execute('SELECT * FROM events')
    events = cursor.fetchall()
    
    conn.close()

    events_list = []

    for i in events:
        event = {
            "id": i[0],
            "event_name": i[1],
            "event_date": i[2],
            "event_location": i[3]
        }
        events_list.append(event)

    return events_list

def delete_event(event_id):
    conn = sqlite3.connect('data/events.db')
    cursor = conn.cursor()

    # Delete the event with the specified ID
    try:
        cursor.execute('DELETE FROM events WHERE id = ?', (event_id,))
        if cursor.rowcount == 0:
            raise ValueError(f"No event found with ID {event_id}")
    except ValueError as e:
        raise e

    conn.commit()
    conn.close()