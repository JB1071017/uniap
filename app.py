from flask import Flask, request, jsonify, render_template
import requests
import json
import re
import secrets
import string
import os
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import time
from datetime import datetime, timedelta
from collections import defaultdict

app = Flask(__name__)
app.secret_key = 'json-extractor-secret-key-2024'

# Storage files
API_KEYS_FILE = 'api_keys.json'
USAGE_FILE = 'usage_stats.json'

def load_data(filename):
    """Load data from JSON file, create if doesn't exist"""
    try:
        with open(filename, 'r') as f:
            content = f.read().strip()
            if not content:  # If file is empty
                return {}
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        # Create file if it doesn't exist or is invalid
        with open(filename, 'w') as f:
            json.dump({}, f)
        return {}

def save_data(filename, data):
    """Save data to JSON file"""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

def generate_api_key():
    """Generate a random API key"""
    charset = string.ascii_letters + string.digits
    return 'api_' + ''.join(secrets.choice(charset) for _ in range(32))

class APIKeyManager:
    def __init__(self):
        # Initialize with empty data if files don't exist
        self.api_keys = load_data(API_KEYS_FILE)
        self.usage_data = load_data(USAGE_FILE)
    
    def create_api_key(self, user_email, plan_type='free'):
        """Create a new API key for user"""
        api_key = generate_api_key()
        
        # Set limits based on plan
        limits = {
            'free': {'requests_per_day': 100, 'rate_limit': 10},
            'premium': {'requests_per_day': 1000, 'rate_limit': 100},
            'enterprise': {'requests_per_day': 10000, 'rate_limit': 1000}
        }
        
        plan_limits = limits.get(plan_type, limits['free'])
        
        key_data = {
            'user_email': user_email,
            'api_key': api_key,
            'plan_type': plan_type,
            'requests_per_day': plan_limits['requests_per_day'],
            'rate_limit': plan_limits['rate_limit'],
            'created_at': datetime.now().isoformat(),
            'is_active': True,
            'total_requests': 0,
            'last_used': None
        }
        
        self.api_keys[api_key] = key_data
        save_data(API_KEYS_FILE, self.api_keys)
        
        return api_key, key_data
    
    def validate_api_key(self, api_key):
        """Validate API key and check limits"""
        if api_key not in self.api_keys:
            return False, "Invalid API key"
        
        key_data = self.api_keys[api_key]
        
        if not key_data['is_active']:
            return False, "API key is inactive"
        
        # Check daily limit
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.usage_data:
            self.usage_data[today] = {}
        
        if api_key not in self.usage_data[today]:
            self.usage_data[today][api_key] = 0
        
        if self.usage_data[today][api_key] >= key_data['requests_per_day']:
            return False, "Daily request limit exceeded"
        
        return True, key_data
    
    def record_usage(self, api_key):
        """Record API usage"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        if today not in self.usage_data:
            self.usage_data[today] = {}
        
        if api_key not in self.usage_data[today]:
            self.usage_data[today][api_key] = 0
        
        self.usage_data[today][api_key] += 1
        self.api_keys[api_key]['total_requests'] += 1
        self.api_keys[api_key]['last_used'] = datetime.now().isoformat()
        
        save_data(USAGE_FILE, self.usage_data)
        save_data(API_KEYS_FILE, self.api_keys)
    
    def get_key_stats(self, api_key):
        """Get usage statistics for API key"""
        if api_key not in self.api_keys:
            return None
        
        key_data = self.api_keys[api_key]
        today = datetime.now().strftime('%Y-%m-%d')
        
        today_usage = self.usage_data.get(today, {}).get(api_key, 0)
        
        return {
            'api_key': api_key,
            'user_email': key_data['user_email'],
            'plan_type': key_data['plan_type'],
            'today_usage': today_usage,
            'daily_limit': key_data['requests_per_day'],
            'total_requests': key_data['total_requests'],
            'last_used': key_data['last_used'],
            'created_at': key_data['created_at']
        }

class UniversalJSONExtractor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
    
    def extract_json_from_url(self, url):
        """Extract JSON data from any URL provided by user"""
        results = {
            'success': False,
            'url': url,
            'data': None,
            'json_objects': [],
            'error': None,
            'content_type': None,
            'size_kb': 0,
            'extraction_method': None,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Validate and clean URL
            parsed_url = urlparse(url)
            if not parsed_url.scheme:
                url = 'https://' + url
            
            # Fetch the content from user-provided URL
            response = self.session.get(url, timeout=15, allow_redirects=True)
            response.raise_for_status()
            
            results['content_type'] = response.headers.get('content-type', '')
            results['size_kb'] = round(len(response.content) / 1024, 2)
            
            # Method 1: Direct JSON response
            if 'application/json' in results['content_type']:
                try:
                    json_data = response.json()
                    results['data'] = json_data
                    results['json_objects'] = [{'type': 'direct_json', 'data': json_data}]
                    results['extraction_method'] = 'direct_json'
                    results['success'] = True
                    return results
                except json.JSONDecodeError:
                    pass
            
            # Method 2: JSON embedded in script tags
            json_objects = self.extract_json_from_script_tags(response.text)
            if json_objects:
                results['json_objects'] = json_objects
                results['extraction_method'] = 'script_tags'
                results['success'] = True
                return results
            
            # Method 3: JSON in JSON-LD format
            json_ld_objects = self.extract_json_ld(response.text)
            if json_ld_objects:
                results['json_objects'] = json_ld_objects
                results['extraction_method'] = 'json_ld'
                results['success'] = True
                return results
            
            # Method 4: Find JSON patterns in text
            text_json_objects = self.extract_json_from_text(response.text)
            if text_json_objects:
                results['json_objects'] = text_json_objects
                results['extraction_method'] = 'text_patterns'
                results['success'] = True
                return results
            
            # Method 5: Try to parse as JSON anyway
            try:
                json_data = response.json()
                results['data'] = json_data
                results['json_objects'] = [{'type': 'forced_json', 'data': json_data}]
                results['extraction_method'] = 'forced_json'
                results['success'] = True
                return results
            except:
                pass
            
            results['error'] = "No JSON data found in the provided URL"
            
        except requests.exceptions.RequestException as e:
            results['error'] = f"Failed to fetch the URL: {str(e)}"
        except Exception as e:
            results['error'] = f"Extraction error: {str(e)}"
        
        return results
    
    def extract_json_from_script_tags(self, html_content):
        """Extract JSON from script tags"""
        soup = BeautifulSoup(html_content, 'html.parser')
        script_tags = soup.find_all('script')
        json_objects = []
        
        for script in script_tags:
            script_content = script.string
            if script_content:
                # Try to find JSON objects in script content
                json_matches = self.find_json_objects(script_content)
                for json_match in json_matches:
                    try:
                        json_data = json.loads(json_match)
                        json_objects.append({
                            'type': 'script_tag',
                            'data': json_data,
                            'script_type': script.get('type', 'unknown')
                        })
                    except:
                        continue
        
        return json_objects
    
    def extract_json_ld(self, html_content):
        """Extract JSON-LD data"""
        soup = BeautifulSoup(html_content, 'html.parser')
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        json_objects = []
        
        for script in json_ld_scripts:
            if script.string:
                try:
                    json_data = json.loads(script.string)
                    json_objects.append({
                        'type': 'json_ld',
                        'data': json_data
                    })
                except:
                    continue
        
        return json_objects
    
    def extract_json_from_text(self, text):
        """Extract JSON patterns from plain text"""
        json_objects = []
        json_matches = self.find_json_objects(text)
        
        for json_match in json_matches:
            try:
                json_data = json.loads(json_match)
                json_objects.append({
                    'type': 'text_pattern',
                    'data': json_data
                })
            except:
                continue
        
        return json_objects
    
    def find_json_objects(self, text):
        """Find potential JSON objects in text"""
        patterns = [
            r'\{[^{}]*\{[^{}]*\}[^{}]*\}',
            r'\{[^{}]*"[^{}]*":[^{}]*\}[^{}]*',
            r'\[[^\[\]]*\{[^\[\]]*\}[^\[\]]*\]',
        ]
        
        matches = []
        for pattern in patterns:
            found = re.findall(pattern, text)
            matches.extend(found)
        
        return matches

# Initialize managers
key_manager = APIKeyManager()
extractor = UniversalJSONExtractor()

# Web Routes
@app.route('/')
def index():
    """Main website page"""
    return render_template('index.html')

@app.route('/generate-key', methods=['POST'])
def generate_key():
    """Generate new API key"""
    data = request.json
    user_email = data.get('email', 'anonymous@example.com')
    plan_type = data.get('plan_type', 'free')
    
    api_key, key_data = key_manager.create_api_key(user_email, plan_type)
    
    return jsonify({
        'success': True,
        'api_key': api_key,
        'key_data': key_data,
        'message': 'API key generated successfully! Save this key - it will only be shown once.'
    })

@app.route('/key-stats', methods=['POST'])
def key_stats():
    """Get API key statistics"""
    data = request.json
    api_key = data.get('api_key')
    
    stats = key_manager.get_key_stats(api_key)
    if stats:
        return jsonify({'success': True, 'stats': stats})
    else:
        return jsonify({'success': False, 'error': 'Invalid API key'})

# API Routes (for users with API keys)
@app.route('/api/extract', methods=['POST'])
def api_extract_json():
    """API endpoint to extract JSON from ANY URL provided by user (requires API key)"""
    # Get API key from header
    api_key = request.headers.get('X-API-Key') or request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not api_key:
        return jsonify({'success': False, 'error': 'API key required'}), 401
    
    # Validate API key
    is_valid, message = key_manager.validate_api_key(api_key)
    if not is_valid:
        return jsonify({'success': False, 'error': message}), 401
    
    # Get request data
    data = request.json
    if not data:
        return jsonify({'success': False, 'error': 'JSON data required'}), 400
    
    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'error': 'URL parameter required'}), 400
    
    # Extract JSON from user-provided URL
    result = extractor.extract_json_from_url(url)
    
    # Record usage
    key_manager.record_usage(api_key)
    
    return jsonify(result)

@app.route('/api/stats', methods=['GET'])
def api_get_stats():
    """Get API usage statistics (requires API key)"""
    api_key = request.headers.get('X-API-Key') or request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not api_key:
        return jsonify({'success': False, 'error': 'API key required'}), 401
    
    stats = key_manager.get_key_stats(api_key)
    if stats:
        return jsonify({'success': True, 'stats': stats})
    else:
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401

# Public endpoint to test without API key
@app.route('/api/public-extract', methods=['POST'])
def public_extract_json():
    """Public endpoint for testing (no API key required, limited use)"""
    data = request.json
    if not data:
        return jsonify({'success': False, 'error': 'JSON data required'}), 400
    
    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'error': 'URL parameter required'}), 400
    
    # For demo purposes, we'll allow public testing
    result = extractor.extract_json_from_url(url)
    
    return jsonify(result)

# Example URLs for testing
@app.route('/example-urls')
def example_urls():
    """Get example URLs for testing"""
    examples = [
        {
            'name': 'GitHub User API',
            'url': 'https://api.github.com/users/octocat',
            'description': 'GitHub user information (Always works)'
        },
        {
            'name': 'JSONPlaceholder Posts',
            'url': 'https://jsonplaceholder.typicode.com/posts/1',
            'description': 'Sample blog post data'
        },
        {
            'name': 'Random Dog Images',
            'url': 'https://dog.ceo/api/breeds/image/random',
            'description': 'Random dog pictures API'
        },
        {
            'name': 'Random Jokes',
            'url': 'https://official-joke-api.appspot.com/random_joke',
            'description': 'Random programming jokes'
        },
        {
            'name': 'Countries Info',
            'url': 'https://restcountries.com/v3.1/name/united%20states',
            'description': 'Country information API'
        }
    ]
    
    return jsonify({'examples': examples})

# Health check endpoint
@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'api_keys_count': len(key_manager.api_keys)
    })

if __name__ == '__main__':
    print("🚀 Starting JSON Extractor API Server...")
    print("📁 Creating necessary files...")
    
    # Ensure files exist
    load_data(API_KEYS_FILE)
    load_data(USAGE_FILE)
    
    print("✅ Files initialized successfully!")
    print("🌐 Server running on http://localhost:5000")
    print("📚 Visit http://localhost:5000 to use the web interface")
    print("🔧 API endpoints available at /api/extract and /api/stats")
    
    app.run(debug=True, host='0.0.0.0', port=5000)