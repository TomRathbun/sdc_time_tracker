import sqlite3
from passlib.hash import pbkdf2_sha256

def setup_user():
    conn = sqlite3.connect('sdc_time.db')
    cursor = conn.cursor()
    
    # Check if there is a manager
    cursor.execute("SELECT id, name FROM employees WHERE role='manager' LIMIT 1")
    manager = cursor.fetchone()
    
    if not manager:
        print("No manager found, looking for any employee")
        cursor.execute("SELECT id, name FROM employees LIMIT 1")
        manager = cursor.fetchone()
        
    if not manager:
        print("No employees found!")
        return
        
    user_id = manager[0]
    user_name = manager[1]
    
    new_pin_hash = pbkdf2_sha256.hash("1234")
    
    cursor.execute("UPDATE employees SET pin_hash=?, pin_needs_reset=0 WHERE id=?", (new_pin_hash, user_id))
    conn.commit()
    conn.close()
    
    print(f"User {user_name} (ID: {user_id}) updated with PIN 1234")
    
    with open('test_user_id.txt', 'w') as f:
        f.write(str(user_id))

if __name__ == '__main__':
    setup_user()
