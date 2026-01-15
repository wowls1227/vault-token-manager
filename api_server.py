"""
Vault Token 기반 인증 API 서버 with Token Management UI

이 서버는:
1. Vault 토큰을 생성/관리하는 웹 UI 제공
2. 클라이언트 토큰 검증 API 제공
3. 서버 자체 토큰(RENEWAL_TOKEN)을 자동으로 갱신
"""

from flask import Flask, request, jsonify, render_template_string
import requests
import os
import sys
import threading
import time
from datetime import datetime

app = Flask(__name__)

# Vault 서버 주소
VAULT_ADDR = os.getenv('VAULT_ADDR', 'http://127.0.0.1:8200')
# 서버가 토큰 생성/관리에 사용하는 토큰 (vault token 생성, 조회 권한 필요, orphan 토큰 필요)
RENEWAL_TOKEN = os.getenv('RENEWAL_TOKEN', 'RENEWAL_TOKEN')

# 로깅 설정
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 전역 변수: 현재 사용 중인 토큰과 TTL
current_token = RENEWAL_TOKEN
token_lock = threading.Lock()


def get_token_info(token):
    """
    토큰의 상세 정보를 조회
    
    Args:
        token (str): 조회할 Vault 토큰
        
    Returns:
        dict or None: 토큰 정보 또는 None (실패 시)
    """
    try:
        response = requests.get(
            f'{VAULT_ADDR}/v1/auth/token/lookup-self',
            headers={'X-Vault-Token': token},
            timeout=5
        )
        
        if response.status_code == 200:
            return response.json()['data']
        return None
    except Exception as e:
        logger.error(f"토큰 정보 조회 실패: {e}")
        return None


def renew_token(token):
    """
    토큰 갱신
    
    Args:
        token (str): 갱신할 Vault 토큰
        
    Returns:
        bool: 성공 여부
    """
    try:
        logger.info("System - 토큰 갱신 시도...")
        response = requests.post(
            f'{VAULT_ADDR}/v1/auth/token/renew-self',
            headers={'X-Vault-Token': token},
            timeout=5
        )
        
        if response.status_code == 200:
            logger.info("System - ✅ 토큰 갱신 성공")
            return True
        else:
            logger.warning(f"System - 토큰 갱신 실패: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"System - 토큰 갱신 중 오류: {e}")
        return False


def token_renewal_worker():
    """
    백그라운드 스레드에서 API 서버가 사용할 토큰을 주기적으로 갱신하는 워커
    
    토큰 수명의 2/3 지점에서 자동 갱신
    """
    global current_token
    
    logger.info("🔄 토큰 갱신 워커 시작")
    
    while True:
        try:
            with token_lock:
                token_to_check = current_token
            
            # 현재 토큰 정보 조회
            token_info = get_token_info(token_to_check)
            
            if not token_info:
                logger.error("System - 토큰 정보를 가져올 수 없습니다. 5초 후 재시도...")
                time.sleep(5)
                continue
            
            # TTL 정보 추출 (초 단위)
            ttl = token_info.get('ttl', 0)
            creation_ttl = token_info.get('creation_ttl', 0)
            
            if ttl == 0 or creation_ttl == 0:
                logger.warning("System - TTL 정보가 없습니다. 토큰이 갱신 불가능할 수 있습니다.")
                time.sleep(10)
                continue
            
            # 수명의 2/3 지점 계산
            renewal_threshold = creation_ttl * 2 / 3
            remaining_time = ttl
            
            logger.info(f"System - 토큰 상태 - 남은 시간: {remaining_time}초 / 전체: {creation_ttl}초")
            
            # 2/3 지점 도달 시 갱신
            if remaining_time <= (creation_ttl - renewal_threshold):
                logger.warning(f"System - ⚠️  토큰 갱신 필요 (남은 시간: {remaining_time}초)")
                
                with token_lock:
                    if renew_token(current_token):
                        logger.info("System - ✅ 토큰 갱신 완료")
                    else:
                        logger.error("System - ❌ 토큰 갱신 실패")
            
            # 10초마다 체크
            time.sleep(10)
            
        except Exception as e:
            logger.error(f"System - 토큰 갱신 워커 오류: {e}")
            time.sleep(10)


def verify_token(token):
    """
    API 서버가 사용하는 토큰의 유효성을 검증하는 함수
    
    Args:
        token (str): 검증할 Vault 토큰
        
    Returns:
        tuple: (is_valid: bool, token_info: dict or None)
    """
    try:
        logger.info(f"System - 토큰 검증 시작: {token[:10]}...")
        
        response = requests.get(
            f'{VAULT_ADDR}/v1/auth/token/lookup-self',
            headers={'X-Vault-Token': token},
            timeout=5
        )
        
        if response.status_code == 200:
            logger.info("System - 토큰 검증 성공")
            return True, response.json()
        else:
            logger.warning(f"System - 토큰 검증 실패: HTTP {response.status_code}")
            return False, None
            
    except Exception as e:
        logger.error(f"System - 토큰 검증 중 오류 발생: {e}")
        return False, None


def create_vault_token(display_name, permissions, ttl='1h'):
    """
    API 서버가 요청 받은 토큰을 Vault에서 생성
    체크한 권한 값은 metadata로 vault token에 같이 저장
    
    Args:
        display_name (str): 토큰 표시 이름
        permissions (dict): 권한 딕셔너리 (예: {'create': True, 'read': True})
        ttl (str): 토큰 유효 시간
        
    Returns:
        dict: {'success': bool, 'token': str, 'message': str}
    """
    try:
        with token_lock:
            auth_token = current_token
        
        # metadata 구성 (체크된 권한만 true로 설정)
        metadata = {k: 'true' for k, v in permissions.items() if v}
        
        # 토큰 생성 요청
        payload = {
            'display_name': display_name,
            'ttl': ttl,
            'meta': metadata
        }
        
        logger.info(f"API - 토큰 생성 요청: display_name={display_name}, metadata={metadata}")
        
        response = requests.post(
            f'{VAULT_ADDR}/v1/auth/token/create',
            headers={'X-Vault-Token': auth_token},
            json=payload,
            timeout=5
        )
        
        if response.status_code == 200:
            result = response.json()
            token = result['auth']['client_token']
            logger.info(f"API - ✅ 토큰 생성 성공: {token[:10]}...")
            
            return {
                'success': True,
                'token': token,
                'message': 'api 토큰이 성공적으로 생성되었습니다'
            }
        else:
            error_msg = f"API - 토큰 생성 실패: HTTP {response.status_code}"
            logger.error(error_msg)
            return {
                'success': False,
                'token': None,
                'message': error_msg
            }
            
    except Exception as e:
        error_msg = f"API - 토큰 생성 중 오류: {e}"
        logger.error(error_msg)
        return {
            'success': False,
            'token': None,
            'message': error_msg
        }


# ============== 웹 UI ==============

@app.route('/')
def index():
    """토큰 생성 UI 페이지"""
    html = """
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Vault Token Manager</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            
            .container {
                background: white;
                border-radius: 15px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                padding: 40px;
                max-width: 600px;
                width: 100%;
            }
            
            h1 {
                color: #667eea;
                margin-bottom: 10px;
                font-size: 28px;
                text-align: center;
            }
            
            .subtitle {
                color: #666;
                text-align: center;
                margin-bottom: 30px;
                font-size: 14px;
            }
            
            .form-group {
                margin-bottom: 25px;
            }
            
            label {
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: 600;
                font-size: 14px;
            }
            
            input[type="text"] {
                width: 100%;
                padding: 12px 15px;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                font-size: 14px;
                transition: border-color 0.3s;
            }
            
            input[type="text"]:focus {
                outline: none;
                border-color: #667eea;
            }
            
            .permissions {
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                padding: 20px;
                background: #f9f9f9;
            }
            
            .permissions-title {
                margin-bottom: 15px;
                font-weight: 600;
                color: #333;
            }
            
            .checkbox-group {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
                gap: 12px;
            }
            
            .checkbox-item {
                display: flex;
                align-items: center;
            }
            
            input[type="checkbox"] {
                width: 18px;
                height: 18px;
                margin-right: 8px;
                cursor: pointer;
                accent-color: #667eea;
            }
            
            .checkbox-item label {
                margin: 0;
                cursor: pointer;
                font-weight: normal;
            }
            
            .btn {
                width: 100%;
                padding: 14px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                margin-top: 10px;
            }
            
            .btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
            }
            
            .btn:active {
                transform: translateY(0);
            }
            
            .result {
                margin-top: 25px;
                padding: 20px;
                border-radius: 8px;
                display: none;
            }
            
            .result.success {
                background: #d4edda;
                border: 2px solid #c3e6cb;
                color: #155724;
            }
            
            .result.error {
                background: #f8d7da;
                border: 2px solid #f5c6cb;
                color: #721c24;
            }
            
            .result-title {
                font-weight: 600;
                margin-bottom: 10px;
                font-size: 16px;
            }
            
            .token-display {
                background: white;
                padding: 12px;
                border-radius: 6px;
                word-break: break-all;
                font-family: 'Courier New', monospace;
                font-size: 13px;
                margin-top: 10px;
            }
            
            .copy-btn {
                margin-top: 10px;
                padding: 8px 16px;
                background: #667eea;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 13px;
            }
            
            .copy-btn:hover {
                background: #5568d3;
            }
            
            .loader {
                display: none;
                text-align: center;
                margin-top: 20px;
            }
            
            .spinner {
                border: 3px solid #f3f3f3;
                border-top: 3px solid #667eea;
                border-radius: 50%;
                width: 40px;
                height: 40px;
                animation: spin 1s linear infinite;
                margin: 0 auto;
            }
            
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 Vault Token Manager</h1>
            <p class="subtitle">새로운 Vault 토큰을 생성하세요</p>
            
            <form id="tokenForm">
                <div class="form-group">
                    <label for="name">토큰 이름 (Display Name)</label>
                    <input type="text" id="name" name="name" 
                           placeholder="예: my-application" required>
                </div>
                
                <div class="form-group">
                    <div class="permissions">
                        <div class="permissions-title">권한 선택</div>
                        <div class="checkbox-group">
                            <div class="checkbox-item">
                                <input type="checkbox" id="create" name="create" value="true">
                                <label for="create">Create</label>
                            </div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="read" name="read" value="true">
                                <label for="read">Read</label>
                            </div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="update" name="update" value="true">
                                <label for="update">Update</label>
                            </div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="delete" name="delete" value="true">
                                <label for="delete">Delete</label>
                            </div>
                            <div class="checkbox-item">
                                <input type="checkbox" id="list" name="list" value="true">
                                <label for="list">List</label>
                            </div>
                        </div>
                    </div>
                </div>
                
                <button type="submit" class="btn">토큰 생성</button>
            </form>
            
            <div class="loader" id="loader">
                <div class="spinner"></div>
                <p style="margin-top: 10px; color: #666;">토큰 생성 중...</p>
            </div>
            
            <div class="result" id="result"></div>
        </div>
        
        <script>
            document.getElementById('tokenForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                
                const name = document.getElementById('name').value;
                const permissions = {
                    create: document.getElementById('create').checked,
                    read: document.getElementById('read').checked,
                    update: document.getElementById('update').checked,
                    delete: document.getElementById('delete').checked,
                    list: document.getElementById('list').checked
                };
                
                // 로딩 표시
                document.getElementById('loader').style.display = 'block';
                document.getElementById('result').style.display = 'none';
                
                try {
                    const response = await fetch('/api/token/create', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            name: name,
                            permissions: permissions
                        })
                    });
                    
                    const data = await response.json();
                    
                    // 로딩 숨김
                    document.getElementById('loader').style.display = 'none';
                    
                    const resultDiv = document.getElementById('result');
                    resultDiv.style.display = 'block';
                    
                    if (data.success) {
                        resultDiv.className = 'result success';
                        resultDiv.innerHTML = `
                            <div class="result-title">✅ ${data.message}</div>
                            <div class="token-display" id="tokenValue">${data.token}</div>
                            <button class="copy-btn" onclick="copyToken()">📋 복사</button>
                        `;
                    } else {
                        resultDiv.className = 'result error';
                        resultDiv.innerHTML = `
                            <div class="result-title">❌ 오류 발생</div>
                            <p>${data.message}</p>
                        `;
                    }
                } catch (error) {
                    document.getElementById('loader').style.display = 'none';
                    const resultDiv = document.getElementById('result');
                    resultDiv.style.display = 'block';
                    resultDiv.className = 'result error';
                    resultDiv.innerHTML = `
                        <div class="result-title">❌ 오류 발생</div>
                        <p>서버 연결 실패: ${error.message}</p>
                    `;
                }
            });
            
            function copyToken() {
                const tokenValue = document.getElementById('tokenValue').innerText;
                navigator.clipboard.writeText(tokenValue).then(() => {
                    alert('토큰이 클립보드에 복사되었습니다!');
                });
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


# ============== API 엔드포인트 ==============

@app.route('/api/token/create', methods=['POST'])
def api_create_token():
    """
    토큰 생성 API
    
    Request Body:
        {
            "name": "토큰 이름",
            "permissions": {
                "create": true,
                "read": false,
                "update": true,
                "delete": false,
                "list": true
            }
        }
    """
    try:
        data = request.get_json()
        name = data.get('name')
        permissions = data.get('permissions', {})
        
        if not name:
            return jsonify({
                'success': False,
                'message': '토큰 이름은 필수입니다'
            }), 400
        
        # 토큰 생성
        result = create_vault_token(name, permissions)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"API 오류: {e}")
        return jsonify({
            'success': False,
            'message': f'서버 오류: {str(e)}'
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """서버 상태 확인 엔드포인트"""
    with token_lock:
        token_status = current_token[:10] + "..." if current_token else "None"
    
    return jsonify({
        'status': 'healthy',
        'vault_addr': VAULT_ADDR,
        'renewal_token_status': token_status
    }), 200


@app.route('/api/data', methods=['GET'])
def get_data():
    """
    샘플 API 입력받은 Token 값을 Vault에 유효성 검사 및 정보 조회 후 반환
    보호된 API 엔드포인트 - Vault 토큰 인증 필요
    """
    token = request.headers.get('Token-Header')
    
    if not token:
        logger.warning("API - 토큰이 제공되지 않음")
        return jsonify({
            'error': 'Token is required',
            'message': 'Token-Header 헤더가 필요합니다'
        }), 401
    
    is_valid, token_info = verify_token(token)
    
    if not is_valid:
        logger.warning("API - 유효하지 않은 토큰으로 접근 시도")
        return jsonify({
            'error': 'Invalid token',
            'message': '토큰이 유효하지 않거나 만료되었습니다'
        }), 403
    
    logger.info(f"API 호출 성공 - 사용자: {token_info['data'].get('display_name', 'unknown')}")
    
    return jsonify({
        'message': 'Success!',
        'data': {
            'result': 'Your API result here',
            'timestamp': token_info['data'].get('creation_time', 'unknown'),
            'user': token_info['data'].get('display_name', 'unknown'),
            'ttl': token_info['data'].get('ttl', 0),
            'permissions': token_info['data'].get('meta', {})
        }
    }), 200


@app.errorhandler(404)
def not_found(error):
    """404 에러 핸들러"""
    return jsonify({
        'error': 'Not Found',
        'message': 'API - 요청한 엔드포인트를 찾을 수 없습니다'
    }), 404


@app.errorhandler(500)
def internal_error(error):
    """500 에러 핸들러"""
    logger.error(f"API - 내부 서버 오류: {error}")
    return jsonify({
        'error': 'Internal Server Error',
        'message': '서버 내부 오류가 발생했습니다'
    }), 500


if __name__ == '__main__':
    # Vault 서버 연결 확인
    try:
        response = requests.get(f'{VAULT_ADDR}/v1/sys/health', timeout=5)
        logger.info(f"✅ Vault 서버 연결 확인 완료: {VAULT_ADDR}")
    except Exception as e:
        logger.error(f"❌ Vault 서버 연결 실패: {e}")
        logger.warning("서버를 시작하지만 Vault 연결이 필요합니다")
    
    # RENEWAL_TOKEN 유효성 확인
    token_info = get_token_info(RENEWAL_TOKEN)
    if token_info:
        logger.info(f"✅ RENEWAL_TOKEN 유효성 확인 완료")
        logger.info(f"   - Display Name: {token_info.get('display_name', 'N/A')}")
        logger.info(f"   - TTL: {token_info.get('ttl', 0)}초")
        logger.info(f"   - Creation TTL: {token_info.get('creation_ttl', 0)}초")
    else:
        logger.error("❌ RENEWAL_TOKEN이 유효하지 않습니다!")
        sys.exit(1)
    
    # 토큰 갱신 백그라운드 스레드 시작
    renewal_thread = threading.Thread(target=token_renewal_worker, daemon=True)
    renewal_thread.start()
    logger.info("✅ 토큰 자동 갱신 스레드 시작됨")
    
    # Flask 서버 시작
    logger.info("🚀 API 서버 시작 - http://0.0.0.0:5001")
    logger.info("📱 UI 접속 - http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)