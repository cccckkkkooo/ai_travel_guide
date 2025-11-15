import os
import sys
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import googlemaps
import logging
import requests
from datetime import datetime
import jwt
import hashlib

# Load environment variables
print("📂 Loading configuration from .env...")
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

if not GOOGLE_API_KEY:
    print("\n" + "="*60)
    print("❌ ERROR: GOOGLE_API_KEY not found!")
    print("="*60)
    print("\nPlease create a .env file in the root directory")
    print("Content: GOOGLE_API_KEY=your_key_here")
    print("\n" + "="*60 + "\n")
    sys.exit(1)

print("✅ GOOGLE_API_KEY loaded successfully!\n")

app = Flask(__name__, static_folder='.', static_url_path='')

# CORS configuration
CORS(app, resources={
    r"/api/*": {
        "origins": ["*"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

try:
    gmaps = googlemaps.Client(key=GOOGLE_API_KEY)
    print("✅ Google Maps client initialized\n")
except Exception as e:
    print(f"❌ Error initializing Google Maps: {str(e)}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE SIMULATION ====================

users_db = {}
routes_db = {}
favorites_db = {}

def create_jwt_token(user_id):
    """Create JWT token"""
    return jwt.encode({'user_id': user_id, 'timestamp': datetime.now().isoformat()}, JWT_SECRET, algorithm='HS256')

def verify_jwt_token(token):
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload['user_id']
    except:
        return None

# ==================== HELPER FUNCTIONS ====================

def get_place_details(place_id):
    """Get detailed place information"""
    try:
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            'place_id': place_id,
            'key': GOOGLE_API_KEY,
            'fields': 'name,rating,reviews,formatted_address,opening_hours,formatted_phone_number,website,photos,types,price_level,user_ratings_total,geometry'
        }
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if result['status'] == 'OK':
            place = result['result']
            photos = []
            if 'photos' in place:
                for photo in place['photos'][:3]:
                    photo_ref = photo.get('photo_reference')
                    if photo_ref:
                        photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={photo_ref}&key={GOOGLE_API_KEY}"
                        photos.append(photo_url)
            
            reviews = []
            if 'reviews' in place:
                for review in place['reviews'][:5]:
                    reviews.append({
                        'author': review.get('author_name', 'Anonymous'),
                        'rating': review.get('rating', 0),
                        'text': review.get('text', ''),
                        'time': review.get('relative_time_description', '')
                    })
            
            opening_hours = []
            if 'opening_hours' in place and 'weekday_text' in place['opening_hours']:
                opening_hours = place['opening_hours']['weekday_text']
            
            return {
                'name': place.get('name', ''),
                'rating': place.get('rating', 'N/A'),
                'user_ratings_total': place.get('user_ratings_total', 0),
                'formatted_address': place.get('formatted_address', ''),
                'phone': place.get('formatted_phone_number', 'N/A'),
                'website': place.get('website', 'N/A'),
                'opening_hours': opening_hours,
                'price_level': place.get('price_level', 'N/A'),
                'photos': photos,
                'reviews': reviews,
                'types': place.get('types', [])
            }
        return None
    except Exception as e:
        logger.error(f"Error in get_place_details: {str(e)}")
        return None

def text_search_places(query, location=None):
    """Search places using text query"""
    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {'query': query, 'key': GOOGLE_API_KEY}
        
        if location:
            params['location'] = location
            params['radius'] = 10000
        
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        places = []
        if result['status'] == 'OK':
            for place in result.get('results', [])[:15]:
                places.append({
                    'place_id': place.get('place_id'),
                    'name': place.get('name'),
                    'rating': place.get('rating', 'N/A'),
                    'formatted_address': place.get('formatted_address', '')
                })
        return places
    except Exception as e:
        logger.error(f"Error in text_search_places: {str(e)}")
        return []

def geocode_address(address):
    """Convert address to coordinates"""
    try:
        result = gmaps.geocode(address)
        if result:
            location = result[0]['geometry']['location']
            return f"{location['lat']},{location['lng']}"
        return None
    except Exception as e:
        logger.error(f"Error in geocode: {str(e)}")
        return None

# ==================== API ENDPOINTS ====================

@app.route('/', methods=['GET'])
def index():
    """Serve HTML"""
    try:
        return send_file('index.html', mimetype='text/html')
    except FileNotFoundError:
        return jsonify({'status': 'ok', 'message': '🌍 AI Travel Guide API'}), 200

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'message': 'AI Travel Guide server is running ✅',
        'timestamp': datetime.now().isoformat()
    }), 200

# ==================== AUTH ENDPOINTS ====================

@app.route('/api/register', methods=['POST'])
def register():
    """Register new user"""
    try:
        data = request.json
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        
        if not username or not email or not password:
            return jsonify({'error': 'Missing required fields'}), 400
        
        if username in users_db:
            return jsonify({'error': 'Username already exists'}), 400
        
        user_id = len(users_db) + 1
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        users_db[username] = {
            'id': user_id,
            'username': username,
            'email': email,
            'password': password_hash,
            'is_admin': False,
            'created_at': datetime.now().isoformat()
        }
        
        token = create_jwt_token(user_id)
        
        return jsonify({
            'token': token,
            'user': {'id': user_id, 'username': username, 'email': email, 'is_admin': False}
        }), 201
    except Exception as e:
        logger.error(f"Register error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    """Login user"""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400
        
        if username not in users_db:
            return jsonify({'error': 'Invalid credentials'}), 401
        
        user = users_db[username]
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        if user['password'] != password_hash:
            return jsonify({'error': 'Invalid credentials'}), 401
        
        token = create_jwt_token(user['id'])
        
        return jsonify({
            'token': token,
            'user': {'id': user['id'], 'username': user['username'], 'email': user['email'], 'is_admin': user['is_admin']}
        }), 200
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile', methods=['GET'])
def profile():
    """Get user profile"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_jwt_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        for user in users_db.values():
            if user['id'] == user_id:
                return jsonify({'user': user}), 200
        
        return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== ROUTES ENDPOINTS ====================

@app.route('/api/routes', methods=['GET'])
def get_routes():
    """Get user routes"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_jwt_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        user_routes = [r for r in routes_db.values() if r['user_id'] == user_id]
        return jsonify({'routes': user_routes}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/routes', methods=['POST'])
def create_route():
    """Create new route"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_jwt_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.json
        route_id = len(routes_db) + 1
        
        routes_db[route_id] = {
            'id': route_id,
            'user_id': user_id,
            'route_name': data.get('route_name'),
            'city': data.get('city'),
            'route_data': data.get('route_data', {}),
            'created_at': datetime.now().isoformat()
        }
        
        return jsonify({'message': 'Route saved', 'route_id': route_id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/routes/<int:route_id>', methods=['DELETE'])
def delete_route(route_id):
    """Delete route"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_jwt_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        if route_id in routes_db and routes_db[route_id]['user_id'] == user_id:
            del routes_db[route_id]
            return jsonify({'message': 'Route deleted'}), 200
        
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== TRAVEL ENDPOINTS ====================

@app.route('/api/generate-itinerary', methods=['POST'])
def generate_itinerary():
    """Generate full travel itinerary"""
    try:
        data = request.json
        city = data.get('city')
        
        if not city:
            return jsonify({'error': 'City is required'}), 400
        
        logger.info(f"Generating itinerary for {city}")
        
        location = geocode_address(city)
        if not location:
            return jsonify({'error': f'Could not find city: {city}'}), 404
        
        # Get attractions
        attractions = []
        for query in [f'tourist attractions in {city}', f'museums in {city}']:
            places = text_search_places(query, location)
            for place in places[:5]:
                details = get_place_details(place['place_id'])
                if details:
                    attractions.append({
                        'name': details['name'],
                        'category': 'Sightseeing',
                        'description': f"Popular attraction",
                        'address': details['formatted_address'],
                        'rating': details['rating'],
                        'phone': details['phone'],
                        'website': details['website'],
                        'opening_hours': details['opening_hours'],
                        'photos': details['photos'],
                        'reviews': details['reviews']
                    })
        
        # Get restaurants
        restaurants = []
        for query in [f'best restaurants in {city}']:
            places = text_search_places(query, location)
            for place in places[:3]:
                details = get_place_details(place['place_id'])
                if details:
                    restaurants.append({
                        'name': details['name'],
                        'address': details['formatted_address'],
                        'rating': details['rating'],
                        'phone': details['phone'],
                        'website': details['website'],
                        'opening_hours': details['opening_hours'],
                        'photos': details['photos']
                    })
        
        itinerary = [{
            'day': 1,
            'activities': attractions[:3],
            'restaurants': restaurants[:2]
        }]
        
        return jsonify({
            'city': city,
            'duration_days': 1,
            'itinerary': itinerary,
            'tips': ['🌟 Arrive early', '🗺️ Download offline maps', '💰 Ask locals']
        }), 200
    except Exception as e:
        logger.error(f"Generate itinerary error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
