import os
import sys
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import googlemaps
import logging
import requests
from datetime import datetime, timedelta
import sqlite3
import jwt
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json

# Load environment variables
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret-key-change-in-production')

if not GOOGLE_API_KEY:
    print("❌ ERROR: GOOGLE_API_KEY not set")
    sys.exit(1)

# Init Flask
app = Flask(__name__, static_folder='.', static_url_path='')

# CORS
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
except Exception as e:
    print(f"❌ Google Maps error: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect('travelguide.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS saved_routes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        route_name TEXT NOT NULL,
        city TEXT NOT NULL,
        route_data TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        place_id TEXT NOT NULL,
        place_name TEXT NOT NULL,
        city TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')

    conn.commit()
    conn.close()
    print("✅ Database initialized")

init_db()

# ==================== DECORATORS ====================

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                return jsonify({'error': 'Invalid token format'}), 401

        if not token:
            return jsonify({'error': 'Token is missing'}), 401

        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            current_user_id = data['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token is invalid'}), 401
        except Exception as e:
            logger.error(f"Token decode error: {str(e)}")
            return jsonify({'error': 'Token verification failed'}), 401

        return f(current_user_id, *args, **kwargs)

    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(current_user_id, *args, **kwargs):
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('SELECT is_admin FROM users WHERE id = ?', (current_user_id,))
        result = c.fetchone()
        conn.close()

        if not result or result[0] != 1:
            return jsonify({'error': 'Admin privileges required'}), 403

        return f(current_user_id, *args, **kwargs)

    return decorated

# ==================== HELPER FUNCTIONS ====================

def get_place_details(place_id):
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

# ==================== AUTH ENDPOINTS ====================

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')

        if not username or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400

        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400

        password_hash = generate_password_hash(password)

        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()

        try:
            is_first_user = False
            c.execute('SELECT COUNT(*) FROM users')
            user_count = c.fetchone()[0]
            if user_count == 0:
                is_first_user = True

            c.execute('INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, ?)',
                     (username, email, password_hash, 1 if is_first_user else 0))
            conn.commit()
            user_id = c.lastrowid
            conn.close()

            token = jwt.encode({
                'user_id': user_id,
                'exp': datetime.utcnow() + timedelta(days=30)
            }, JWT_SECRET, algorithm='HS256')

            return jsonify({
                'message': 'User registered successfully',
                'token': token,
                'user': {
                    'id': user_id,
                    'username': username,
                    'email': email,
                    'is_admin': is_first_user
                }
            }), 201
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({'error': 'Username or email already exists'}), 400

    except Exception as e:
        logger.error(f"Register error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400

        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('SELECT id, username, email, password_hash, is_admin FROM users WHERE username = ?', (username,))
        user = c.fetchone()
        conn.close()

        if not user or not check_password_hash(user[3], password):
            return jsonify({'error': 'Invalid credentials'}), 401

        token = jwt.encode({
            'user_id': user[0],
            'exp': datetime.utcnow() + timedelta(days=30)
        }, JWT_SECRET, algorithm='HS256')

        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': {
                'id': user[0],
                'username': user[1],
                'email': user[2],
                'is_admin': user[4]
            }
        }), 200

    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile', methods=['GET'])
@token_required
def get_profile(current_user_id):
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('SELECT id, username, email, is_admin, created_at FROM users WHERE id = ?', (current_user_id,))
        user = c.fetchone()

        if not user:
            conn.close()
            return jsonify({'error': 'User not found'}), 404

        c.execute('SELECT COUNT(*) FROM saved_routes WHERE user_id = ?', (current_user_id,))
        routes_count = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM favorites WHERE user_id = ?', (current_user_id,))
        favorites_count = c.fetchone()[0]

        conn.close()

        return jsonify({
            'user': {
                'id': user[0],
                'username': user[1],
                'email': user[2],
                'is_admin': user[3],
                'created_at': user[4],
                'routes_count': routes_count,
                'favorites_count': favorites_count
            }
        }), 200

    except Exception as e:
        logger.error(f"Profile error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ==================== ADMIN ENDPOINTS ====================

@app.route('/api/admin/users', methods=['GET'])
@token_required
@admin_required
def get_all_users(current_user_id):
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('SELECT id, username, email, is_admin, created_at FROM users ORDER BY created_at DESC')
        users = c.fetchall()
        conn.close()

        users_list = []
        for user in users:
            users_list.append({
                'id': user[0],
                'username': user[1],
                'email': user[2],
                'is_admin': user[3],
                'created_at': user[4]
            })

        return jsonify({'users': users_list}), 200

    except Exception as e:
        logger.error(f"Get users error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_user(current_user_id, user_id):
    try:
        if current_user_id == user_id:
            return jsonify({'error': 'Cannot delete yourself'}), 400

        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('DELETE FROM saved_routes WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM favorites WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()

        return jsonify({'message': 'User deleted successfully'}), 200

    except Exception as e:
        logger.error(f"Delete user error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/all-routes', methods=['GET'])
@token_required
@admin_required
def get_all_routes(current_user_id):
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()

        c.execute('''
            SELECT sr.id, sr.user_id, sr.route_name, sr.city, sr.route_data, sr.created_at, u.username, u.email
            FROM saved_routes sr
            JOIN users u ON sr.user_id = u.id
            ORDER BY sr.created_at DESC
        ''')
        routes = c.fetchall()
        conn.close()

        routes_list = []
        for route in routes:
            routes_list.append({
                'id': route[0],
                'user_id': route[1],
                'route_name': route[2],
                'city': route[3],
                'route_data': json.loads(route[4]),
                'created_at': route[5],
                'username': route[6],
                'email': route[7]
            })

        return jsonify({
            'total_routes': len(routes_list),
            'routes': routes_list
        }), 200

    except Exception as e:
        logger.error(f"Get all routes error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/routes/<int:route_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_route_admin(current_user_id, route_id):
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('DELETE FROM saved_routes WHERE id = ?', (route_id,))
        conn.commit()
        conn.close()

        return jsonify({'message': 'Route deleted successfully'}), 200

    except Exception as e:
        logger.error(f"Delete route error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/stats', methods=['GET'])
@token_required
@admin_required
def get_stats(current_user_id):
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM users')
        total_users = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1')
        total_admins = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM saved_routes')
        total_routes = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM favorites')
        total_favorites = c.fetchone()[0]

        conn.close()

        return jsonify({
            'total_users': total_users,
            'total_admins': total_admins,
            'total_routes': total_routes,
            'total_favorites': total_favorites,
            'top_users': [],
            'top_cities': []
        }), 200

    except Exception as e:
        logger.error(f"Get stats error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ==================== ROUTES ENDPOINTS ====================

@app.route('/api/routes', methods=['GET'])
@token_required
def get_saved_routes(current_user_id):
    """Get user's saved routes"""
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('''SELECT id, route_name, city, route_data, created_at 
                     FROM saved_routes WHERE user_id = ? ORDER BY created_at DESC''',
                  (current_user_id,))
        routes = c.fetchall()
        conn.close()

        routes_list = []
        for route in routes:
            routes_list.append({
                'id': route[0],
                'route_name': route[1],
                'city': route[2],
                'route_data': json.loads(route[3]),
                'created_at': route[4]
            })

        return jsonify({'routes': routes_list}), 200

    except Exception as e:
        logger.error(f"Get routes error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/routes', methods=['POST'])
@token_required
def save_route(current_user_id):
    """Save a new route"""
    try:
        data = request.json
        route_name = data.get('route_name')
        city = data.get('city')
        route_data = data.get('route_data')

        if not route_name or not city or not route_data:
            return jsonify({'error': 'All fields are required'}), 400

        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('''INSERT INTO saved_routes (user_id, route_name, city, route_data) 
                     VALUES (?, ?, ?, ?)''',
                  (current_user_id, route_name, city, json.dumps(route_data)))
        conn.commit()
        route_id = c.lastrowid
        conn.close()

        return jsonify({
            'message': 'Route saved successfully',
            'route_id': route_id
        }), 201

    except Exception as e:
        logger.error(f"Save route error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/routes/<int:route_id>', methods=['DELETE'])
@token_required
def delete_route(current_user_id, route_id):
    """Delete a saved route"""
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('DELETE FROM saved_routes WHERE id = ? AND user_id = ?', (route_id, current_user_id))
        conn.commit()
        conn.close()

        return jsonify({'message': 'Route deleted successfully'}), 200

    except Exception as e:
        logger.error(f"Delete route error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ==================== FAVORITES ENDPOINTS ====================

@app.route('/api/favorites', methods=['GET'])
@token_required
def get_favorites(current_user_id):
    """Get user's favorite places"""
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('''SELECT id, place_id, place_name, city, created_at 
                     FROM favorites WHERE user_id = ? ORDER BY created_at DESC''',
                  (current_user_id,))
        favorites = c.fetchall()
        conn.close()

        favorites_list = []
        for fav in favorites:
            favorites_list.append({
                'id': fav[0],
                'place_id': fav[1],
                'place_name': fav[2],
                'city': fav[3],
                'created_at': fav[4]
            })

        return jsonify({'favorites': favorites_list}), 200

    except Exception as e:
        logger.error(f"Get favorites error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/favorites', methods=['POST'])
@token_required
def add_favorite(current_user_id):
    """Add place to favorites"""
    try:
        data = request.json
        place_id = data.get('place_id')
        place_name = data.get('place_name')
        city = data.get('city')

        if not place_id or not place_name or not city:
            return jsonify({'error': 'All fields are required'}), 400

        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('''INSERT INTO favorites (user_id, place_id, place_name, city) 
                     VALUES (?, ?, ?, ?)''',
                  (current_user_id, place_id, place_name, city))
        conn.commit()
        fav_id = c.lastrowid
        conn.close()

        return jsonify({
            'message': 'Added to favorites',
            'favorite_id': fav_id
        }), 201

    except Exception as e:
        logger.error(f"Add favorite error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/favorites/<int:fav_id>', methods=['DELETE'])
@token_required
def delete_favorite(current_user_id, fav_id):
    """Remove from favorites"""
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()
        c.execute('DELETE FROM favorites WHERE id = ? AND user_id = ?', (fav_id, current_user_id))
        conn.commit()
        conn.close()

        return jsonify({'message': 'Removed from favorites'}), 200

    except Exception as e:
        logger.error(f"Delete favorite error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== TRAVEL API ====================

@app.route('/', methods=['GET'])
def index():
    try:
        return send_file('index.html', mimetype='text/html')
    except FileNotFoundError:
        return jsonify({
            'status': 'success',
            'message': '🌍 AI Travel Guide API v2.0',
            'version': '2.0.0',
        }), 200

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'message': 'AI Travel Guide server is running ✅'
    }), 200

@app.route('/api/generate-itinerary', methods=['POST'])
def generate_itinerary():
    try:
        data = request.json
        city = data.get('city')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        interests = data.get('interests', [])

        if not city:
            return jsonify({'error': 'City is required'}), 400

        geocode_result = geocode_address(city)
        if not geocode_result:
            return jsonify({'error': f'Could not find city: {city}'}), 404

        location = f"{geocode_result['lat']},{geocode_result['lng']}"

        all_attractions = []
        seen_place_ids = set()

        base_queries = [f'tourist attractions in {city}', f'things to do in {city}']

        for query in base_queries[:5]:
            places = text_search_places(query, location)
            for place in places:
                place_id = place['place_id']
                if place_id in seen_place_ids:
                    continue
                seen_place_ids.add(place_id)

                details = get_place_details(place_id)
                if details:
                    all_attractions.append({
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
                        'place_id': place_id
                    })

                    if len(all_attractions) >= 15:
                        break

            if len(all_attractions) >= 15:
                break

        if start_date and end_date:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d')
            duration = (end - start).days + 1
        else:
            duration = 3

        itinerary = []
        for day in range(1, duration + 1):
            itinerary.append({
                'day': day,
                'date': start_date if start_date else f'Day {day}',
                'activities': all_attractions[:3],
                'restaurants': []
            })

        result = {
            'city': city,
            'duration_days': duration,
            'total_attractions': len(all_attractions),
            'total_restaurants': 0,
            'selected_interests': interests,
            'itinerary': itinerary,
            'tips': []
        }

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

@app.route('/api/admin/activity', methods=['GET'])
@token_required
@admin_required
def get_activity(current_user_id):
    """Get platform activity (admin only)"""
    return jsonify({
        'activity': [],
        'message': 'No activity yet'
    }), 200

@app.route('/api/export/db', methods=['GET'])
@token_required
@admin_required
def export_database(current_user_id):
    try:
        conn = sqlite3.connect('travelguide.db')
        c = conn.cursor()

        # Пользователи
        c.execute('SELECT id, username, email, is_admin, created_at FROM users')
        users = [dict(zip(['id','username','email','is_admin','created_at'], row)) for row in c.fetchall()]
        
        # Маршруты
        c.execute('SELECT id, user_id, route_name, city, route_data, created_at FROM saved_routes')
        routes = []
        for row in c.fetchall():
            route = dict(zip(['id','user_id','route_name','city','route_data','created_at'], row))
            route['route_data'] = json.loads(route['route_data'])
            routes.append(route)
        
        # Избранное
        c.execute('SELECT id, user_id, place_id, place_name, city, created_at FROM favorites')
        favorites = [dict(zip(['id','user_id','place_id','place_name','city','created_at'], row)) for row in c.fetchall()]
        
        conn.close()

        return jsonify({
            'users': users,
            'routes': routes,
            'favorites': favorites,
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500



if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
