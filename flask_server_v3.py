import os
import sys
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import googlemaps
import logging
import requests
from datetime import datetime
import hashlib
import base64

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
# In production, use a real database (SQLite, PostgreSQL, etc.)

users_db = {}
routes_db = {}
favorites_db = {}

def generate_simple_token(user_id):
    """Generate a simple token (not JWT, just base64 encoded)"""
    token_data = f"{user_id}:{datetime.now().isoformat()}"
    return base64.b64encode(token_data.encode()).decode()

def verify_token(token):
    """Verify token and extract user_id"""
    try:
        token_data = base64.b64decode(token).decode()
        user_id = token_data.split(':')[0]
        return user_id
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
                'types': place.get('types', []),
                'location': {
                    'lat': place.get('geometry', {}).get('location', {}).get('lat'),
                    'lng': place.get('geometry', {}).get('location', {}).get('lng')
                }
            }
        return None
    except Exception as e:
        logger.error(f"Error in get_place_details: {str(e)}")
        return None

def text_search_places(query, location=None):
    """Search places using text query"""
    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            'query': query,
            'key': GOOGLE_API_KEY
        }
        
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
                    'formatted_address': place.get('formatted_address', ''),
                    'types': place.get('types', []),
                    'location': {
                        'lat': place.get('geometry', {}).get('location', {}).get('lat'),
                        'lng': place.get('geometry', {}).get('location', {}).get('lng')
                    }
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
            return {
                'lat': location['lat'],
                'lng': location['lng'],
                'formatted_address': result[0]['formatted_address']
            }
        return None
    except Exception as e:
        logger.error(f"Error in geocode: {str(e)}")
        return None

def get_directions_info(origin, destination, mode='transit'):
    """Get directions between two places"""
    try:
        result = gmaps.directions(origin, destination, mode=mode)
        if result:
            route = result[0]
            leg = route['legs'][0]
            return {
                'distance': leg.get('distance', {}).get('text', ''),
                'duration': leg.get('duration', {}).get('text', ''),
                'duration_seconds': leg.get('duration', {}).get('value', 0),
                'start_address': leg.get('start_address', ''),
                'end_address': leg.get('end_address', '')
            }
        return None
    except Exception as e:
        logger.error(f"Error in get_directions: {str(e)}")
        return None

# ==================== API ENDPOINTS ====================

@app.route('/', methods=['GET'])
def index():
    """Serve HTML"""
    try:
        return send_file('index.html', mimetype='text/html')
    except FileNotFoundError:
        logger.error("index.html not found")
        return jsonify({
            'status': 'success',
            'message': '🌍 AI Travel Guide API',
            'version': '1.0.0',
            'available_endpoints': {
                'health': '/api/health',
                'register': '/api/register',
                'login': '/api/login',
                'profile': '/api/profile',
                'search_attractions': '/api/search-attractions',
                'search_restaurants': '/api/search-restaurants',
                'generate_itinerary': '/api/generate-itinerary',
                'routes': '/api/routes',
                'favorites': '/api/favorites'
            }
        }), 200

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'message': 'AI Travel Guide server is running ✅',
        'api_configured': True,
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
            'created_at': datetime.now().isoformat(),
            'routes_count': 0,
            'favorites_count': 0
        }
        
        token = generate_simple_token(user_id)
        
        return jsonify({
            'token': token,
            'user': {
                'id': user_id,
                'username': username,
                'email': email,
                'is_admin': False
            }
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
        
        token = generate_simple_token(user['id'])
        
        return jsonify({
            'token': token,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'is_admin': user['is_admin']
            }
        }), 200
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile', methods=['GET'])
def profile():
    """Get user profile"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        for user in users_db.values():
            if user['id'] == int(user_id):
                return jsonify({'user': user}), 200
        
        return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        logger.error(f"Profile error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ==================== ROUTES ENDPOINTS ====================

@app.route('/api/routes', methods=['GET'])
def get_routes():
    """Get user routes"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        user_routes = [r for r in routes_db.values() if r['user_id'] == int(user_id)]
        return jsonify({'routes': user_routes}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/routes', methods=['POST'])
def create_route():
    """Create new route"""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = verify_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.json
        route_id = len(routes_db) + 1
        
        routes_db[route_id] = {
            'id': route_id,
            'user_id': int(user_id),
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
        user_id = verify_token(token)
        
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        if route_id in routes_db and routes_db[route_id]['user_id'] == int(user_id):
            del routes_db[route_id]
            return jsonify({'message': 'Route deleted'}), 200
        
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== TRAVEL ENDPOINTS ====================

@app.route('/api/search-attractions', methods=['POST'])
def search_attractions():
    """Search for tourist attractions"""
    try:
        data = request.json
        city = data.get('city')
        
        if not city:
            return jsonify({'error': 'City is required'}), 400
        
        geocode_result = geocode_address(city)
        if not geocode_result:
            return jsonify({'error': f'Could not find city: {city}'}), 404
        
        location = f"{geocode_result['lat']},{geocode_result['lng']}"
        attractions = []
        search_queries = [
            f'tourist attractions in {city}',
            f'museums in {city}',
            f'historical sites in {city}',
        ]
        
        seen_place_ids = set()
        for query in search_queries[:3]:
            places = text_search_places(query, location)
            for place in places:
                place_id = place['place_id']
                if place_id in seen_place_ids:
                    continue
                seen_place_ids.add(place_id)
                
                details = get_place_details(place_id)
                if details:
                    attractions.append({
                        'place_id': place_id,
                        'name': details['name'],
                        'category': 'Sightseeing',
                        'description': f"Popular attraction with {details['user_ratings_total']} reviews",
                        'location': details['formatted_address'],
                        'address': details['formatted_address'],
                        'rating': details['rating'],
                        'phone': details['phone'],
                        'website': details['website'],
                        'opening_hours': details['opening_hours'],
                        'photos': details['photos'],
                        'reviews': details['reviews'],
                        'duration': '2h',
                    })
                
                if len(attractions) >= 12:
                    break
            if len(attractions) >= 12:
                break
        
        logger.info(f"Found {len(attractions)} attractions for {city}")
        return jsonify({'attractions': attractions}), 200
    except Exception as e:
        logger.error(f"Search attractions error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate-itinerary', methods=['POST'])
def generate_itinerary():
    """Generate full travel itinerary"""
    try:
        data = request.json
        city = data.get('city')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if not city:
            return jsonify({'error': 'City is required'}), 400
        
        logger.info(f"Generating itinerary for {city}")
        
        geocode_result = geocode_address(city)
        if not geocode_result:
            return jsonify({'error': f'Could not find city: {city}'}), 404
        
        location = f"{geocode_result['lat']},{geocode_result['lng']}"
        
        # Get attractions
        attractions = []
        search_queries = [
            f'tourist attractions in {city}',
            f'museums in {city}',
        ]
        
        seen_place_ids = set()
        for query in search_queries:
            places = text_search_places(query, location)
            for place in places:
                place_id = place['place_id']
                if place_id in seen_place_ids:
                    continue
                seen_place_ids.add(place_id)
                
                details = get_place_details(place_id)
                if details:
                    attractions.append({
                        'name': details['name'],
                        'category': 'Sightseeing',
                        'description': f"Popular attraction with {details['user_ratings_total']} reviews",
                        'address': details['formatted_address'],
                        'rating': details['rating'],
                        'phone': details['phone'],
                        'website': details['website'],
                        'opening_hours': details['opening_hours'],
                        'photos': details['photos'],
                        'reviews': details['reviews'],
                        'duration': '2h',
                    })
                if len(attractions) >= 10:
                    break
            if len(attractions) >= 10:
                break
        
        # Get restaurants
        restaurants = []
        search_queries_rest = [
            f'best restaurants in {city}',
            f'top rated restaurants in {city}'
        ]
        
        seen_place_ids = set()
        for query in search_queries_rest:
            places = text_search_places(query, location)
            for place in places:
                place_id = place['place_id']
                if place_id in seen_place_ids:
                    continue
                seen_place_ids.add(place_id)
                
                details = get_place_details(place_id)
                if details:
                    price_map = {1: '$', 2: '$$', 3: '$$$', 4: '$$$$'}
                    price = price_map.get(details['price_level'], 'N/A') if isinstance(details['price_level'], int) else 'N/A'
                    
                    restaurants.append({
                        'name': details['name'],
                        'address': details['formatted_address'],
                        'price': price,
                        'rating': details['rating'],
                        'phone': details['phone'],
                        'website': details['website'],
                        'opening_hours': details['opening_hours'],
                        'photos': details['photos'],
                        'reviews': details['reviews'],
                    })
                if len(restaurants) >= 6:
                    break
            if len(restaurants) >= 6:
                break
        
        # Calculate duration
        if start_date and end_date:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d')
            duration = (end - start).days + 1
        else:
            duration = 3
        
        # Build itinerary
        activities_per_day = max(2, len(attractions) // duration) if duration > 0 else 2
        restaurants_per_day = max(1, len(restaurants) // duration) if duration > 0 else 1
        
        itinerary = []
        for day in range(1, duration + 1):
            day_start = (day - 1) * activities_per_day
            day_end = min(day * activities_per_day, len(attractions))
            rest_start = (day - 1) * restaurants_per_day
            rest_end = min(day * restaurants_per_day, len(restaurants))
            
            day_activities = attractions[day_start:day_end]
            day_restaurants = restaurants[rest_start:rest_end]
            
            itinerary.append({
                'day': day,
                'date': start_date if start_date else f'Day {day}',
                'activities': day_activities,
                'restaurants': day_restaurants
            })
        
        result = {
            'city': city,
            'duration_days': duration,
            'total_attractions': len(attractions),
            'total_restaurants': len(restaurants),
            'itinerary': itinerary,
            'tips': [
                '🌟 Arrive early at popular attractions',
                '🗺️ Download offline maps',
                '💰 Ask locals for recommendations',
                '🍽️ Try street food',
                '🎭 Visit free museums'
            ]
        }
        
        logger.info(f"Generated itinerary: {duration} days")
        return jsonify(result), 200
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
