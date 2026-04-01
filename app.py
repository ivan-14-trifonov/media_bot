from flask import Flask, jsonify, request
import datetime
import os
import platform

app = Flask(__name__)

# Главная страница
@app.route('/')
def home():
    return """
    <html>
        <head>
            <title>Мой Docker Python сервер</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    text-align: center;
                    margin-top: 50px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }
                .container {
                    background: rgba(0,0,0,0.7);
                    padding: 30px;
                    border-radius: 15px;
                    display: inline-block;
                    max-width: 600px;
                }
                h1 { color: #ffd700; }
                a {
                    color: #ffd700;
                    text-decoration: none;
                }
                a:hover {
                    text-decoration: underline;
                }
                button {
                    background: #ffd700;
                    color: #333;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 5px;
                    cursor: pointer;
                    margin-top: 20px;
                }
                button:hover {
                    background: #ffed4a;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🐳 Docker Python Сервер 🐍</h1>
                <p>Привет! Этот сервер работает внутри Docker контейнера!</p>
                <p>📅 Текущее время: <strong id="time">загрузка...</strong></p>
                <p>🏷️ Имя контейнера: <strong>""" + os.uname().nodename + """</strong></p>
                <p>💻 Операционная система: <strong>""" + platform.system() + " " + platform.release() + """</strong></p>
                <p>🐍 Версия Python: <strong>""" + platform.python_version() + """</strong></p>
                <button onclick="fetchData()">📡 Получить данные с API</button>
                <div id="api-data" style="margin-top: 20px;"></div>
                <hr>
                <p>
                    📚 <a href="/api/info">API информация</a> | 
                    🔧 <a href="/health">Health check</a>
                </p>
            </div>
            <script>
                function updateTime() {
                    fetch('/api/time')
                        .then(response => response.json())
                        .then(data => {
                            document.getElementById('time').textContent = data.time;
                        });
                }
                setInterval(updateTime, 1000);
                updateTime();
                
                function fetchData() {
                    fetch('/api/data')
                        .then(response => response.json())
                        .then(data => {
                            const div = document.getElementById('api-data');
                            div.innerHTML = '<div style="background: white; color: #333; padding: 10px; border-radius: 5px;"><strong>Данные с API:</strong><br>' + 
                                'Сообщение: ' + data.message + '<br>' +
                                'Сервер работает: ' + data.uptime + ' секунд</div>';
                        });
                }
            </script>
        </body>
    </html>
    """

# API: текущее время
@app.route('/api/time')
def api_time():
    return jsonify({
        'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'timestamp': datetime.datetime.now().isoformat(),
        'timezone': str(datetime.datetime.now().astimezone().tzinfo)
    })

# API: информация о сервере
@app.route('/api/info')
def api_info():
    return jsonify({
        'server': 'Docker Python Server',
        'version': '1.0.0',
        'hostname': os.uname().nodename,
        'python_version': platform.python_version(),
        'os': platform.system(),
        'os_release': platform.release(),
        'docker': True
    })

# Health check для оркестрации
@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'uptime': str(datetime.datetime.now() - start_time)
    }), 200

# API: пример данных
@app.route('/api/data')
def api_data():
    return jsonify({
        'message': 'Привет из Docker контейнера!',
        'data': [1, 2, 3, 4, 5],
        'uptime': (datetime.datetime.now() - start_time).total_seconds(),
        'status': 'success'
    })

# API: echo (отправляет обратно то, что вы отправили)
@app.route('/api/echo', methods=['POST'])
def echo():
    data = request.get_json()
    return jsonify({
        'echo': data,
        'received_at': datetime.datetime.now().isoformat()
    })

# Счетчик запросов (демонстрация состояния)
request_counter = 0

@app.before_request
def count_requests():
    global request_counter
    request_counter += 1

@app.route('/api/stats')
def stats():
    return jsonify({
        'total_requests': request_counter,
        'uptime_seconds': (datetime.datetime.now() - start_time).total_seconds()
    })

# Запуск сервера
if __name__ == '__main__':
    start_time = datetime.datetime.now()
    print("=" * 50)
    print("🚀 Docker Python Сервер запущен!")
    print("=" * 50)
    print(f"📅 Время запуска: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🏷️  Имя хоста: {os.uname().nodename}")
    print(f"🐍 Версия Python: {platform.python_version()}")
    print(f"🌐 Сервер доступен на http://0.0.0.0:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)