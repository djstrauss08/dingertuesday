from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return "Hello from MLB Stats App! This is working!"

@app.route('/test')
def test():
    return {"status": "success", "message": "API is working"}

if __name__ == '__main__':
    app.run() 