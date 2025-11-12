from flask import Flask, request, jsonify
import os

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('files')
    for f in files:
        f.save(os.path.join(UPLOAD_FOLDER, f.filename))
    return jsonify({'message': 'Files uploaded successfully'})

if __name__ == '__main__':
    app.run(debug=True)
