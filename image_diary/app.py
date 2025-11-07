from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import os
from datetime import datetime
import exifread
from geopy.geocoders import Nominatim
import folium
from folium.plugins import MarkerCluster

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/images'

def init_db():
    conn = sqlite3.connect('diary.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS diary_entries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT,
                  content TEXT,
                  image_path TEXT,
                  date_taken DATETIME,
                  latitude REAL,
                  longitude REAL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def get_exif_data(image_path):
    with open(image_path, 'rb') as f:
        tags = exifread.process_file(f)
        
    date_taken = None
    latitude = None
    longitude = None
    
    if 'EXIF DateTimeOriginal' in tags:
        date_str = str(tags['EXIF DateTimeOriginal'])
        date_taken = datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
    
    if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
        lat = tags['GPS GPSLatitude'].values
        long = tags['GPS GPSLongitude'].values
        
        lat_ref = tags['GPS GPSLatitudeRef'].values
        long_ref = tags['GPS GPSLongitudeRef'].values
        
        latitude = float(lat[0] + lat[1]/60 + lat[2]/3600)
        longitude = float(long[0] + long[1]/60 + long[2]/3600)
        
        if lat_ref == 'S':
            latitude = -latitude
        if long_ref == 'W':
            longitude = -longitude
            
    return date_taken, latitude, longitude

@app.route('/')
def index():
    conn = sqlite3.connect('diary.db')
    c = conn.cursor()
    entries = c.execute('SELECT * FROM diary_entries ORDER BY date_taken DESC').fetchall()
    conn.close()
    
    # Create a map centered on the first entry or default to South Korea
    m = folium.Map(location=[36.5, 127.5], zoom_start=7)
    
    for entry in entries:
        if entry[5] and entry[6]:  # if latitude and longitude exist
            folium.Marker(
                [entry[5], entry[6]],
                popup=f"<b>{entry[1]}</b><br>{entry[3]}<br>{entry[4]}",
                icon=folium.Icon(color='red', icon='info-sign')
            ).add_to(m)
    
    m.save('templates/map.html')
    return render_template('index.html', entries=entries)

@app.route('/add', methods=['GET', 'POST'])
def add_entry():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        image = request.files['image']
        
        if image:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
            image.save(image_path)
            
            date_taken, latitude, longitude = get_exif_data(image_path)
            
            conn = sqlite3.connect('diary.db')
            c = conn.cursor()
            c.execute('''INSERT INTO diary_entries 
                        (title, content, image_path, date_taken, latitude, longitude)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (title, content, image_path, date_taken, latitude, longitude))
            conn.commit()
            conn.close()
            
        return redirect(url_for('index'))
    
    return render_template('add.html')

@app.route('/map')
def show_map():
    return render_template('cluster_map.html')

@app.route('/get_clustered_map')
def get_clustered_map():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    conn = sqlite3.connect('diary.db')
    c = conn.cursor()
    
    query = '''SELECT * FROM diary_entries WHERE date_taken BETWEEN ? AND ?'''
    entries = c.execute(query, (start_date, end_date)).fetchall()
    conn.close()
    
    m = folium.Map(location=[36.5, 127.5], zoom_start=7)
    marker_cluster = MarkerCluster().add_to(m)
    
    for entry in entries:
        if entry[5] and entry[6]:  # if latitude and longitude exist
            folium.Marker(
                [entry[5], entry[6]],
                popup=f"<b>{entry[1]}</b><br>{entry[3]}<br>{entry[4]}",
                icon=folium.Icon(color='red', icon='info-sign')
            ).add_to(marker_cluster)
    
    return m._repr_html_()

if __name__ == '__main__':
    init_db()
    app.run(debug=True)