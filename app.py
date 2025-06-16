import statsapi
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from datetime import date, datetime, timedelta
import traceback
import time
from functools import lru_cache, wraps
import requests_cache
import sqlite3
import json
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
import atexit
import re
import os
import uuid
from werkzeug.utils import secure_filename
import requests
import firebase_admin
from firebase_admin import credentials, auth
import pytz

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize scheduler variable
scheduler = None

# Set up request caching
requests_cache.install_cache('mlb_api_cache', backend='sqlite', expire_after=3600)  # Cache for 1 hour

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Eastern Time zone for MLB operations
EASTERN_TZ = pytz.timezone('US/Eastern')

def get_mlb_today():
    """
    Get the current MLB day based on Eastern Time.
    The MLB day changes at 3 AM ET, so games before 3 AM are considered part of the previous day.
    """
    now_et = datetime.now(EASTERN_TZ)
    
    # If it's before 3 AM ET, use the previous day
    if now_et.hour < 3:
        mlb_date = (now_et - timedelta(days=1)).date()
    else:
        mlb_date = now_et.date()
    
    return mlb_date.strftime("%Y-%m-%d")

def get_eastern_time():
    """Get current Eastern Time"""
    return datetime.now(EASTERN_TZ)

# Initialize Firebase Admin SDK
# For development, we'll use the project configuration without a service account
# In production, you'd want to use a service account JSON file
try:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={
            'projectId': 'dingertuesday-18a26'
        })
    print("Firebase Admin SDK initialized successfully")
except Exception as e:
    print(f"Firebase Admin SDK initialization error: {e}")

def verify_firebase_token(id_token):
    """Verify Firebase ID token server-side using Admin SDK"""
    try:
        # For development, if Firebase Admin SDK isn't properly configured,
        # we'll do a basic validation and trust the frontend
        if not id_token:
            return None
            
        # Try to verify with Firebase Admin SDK
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        logging.error(f"Token verification error: {e}")
        
        # For development mode, if token verification fails due to credentials,
        # but the token looks valid (basic check), we'll allow it
        if id_token and len(id_token) > 100 and '.' in id_token:
            logging.warning("Using development mode: trusting client-side authentication")
            
            # Try to extract email from token payload (basic parsing)
            try:
                import base64
                import json
                # JWT tokens have 3 parts separated by dots
                parts = id_token.split('.')
                if len(parts) >= 2:
                    # Decode the payload (second part)
                    payload = parts[1]
                    # Add padding if needed
                    payload += '=' * (4 - len(payload) % 4)
                    decoded_payload = base64.b64decode(payload)
                    token_data = json.loads(decoded_payload)
                    
                    # If this is danieljstrauss1@gmail.com, always allow
                    if token_data.get('email') == 'danieljstrauss1@gmail.com':
                        return {
                            'uid': f"admin-{token_data.get('email', 'unknown')}",
                            'email': token_data.get('email')
                        }
            except Exception as parse_error:
                logging.error(f"Error parsing token: {parse_error}")
            
            return {'uid': 'dev-user', 'email': 'dev@example.com'}
        
        return None

def get_or_create_user(uid, email):
    """Get or create user in database and return user info"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Try to get existing user by uid first
        cursor.execute('SELECT * FROM users WHERE uid = ?', (uid,))
        user = cursor.fetchone()
        
        if user:
            # Update last login
            cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE uid = ?', (uid,))
            conn.commit()
            return {
                'id': user[0],
                'uid': user[1],
                'email': user[2],
                'role': user[3],
                'created_at': user[4],
                'last_login': user[5]
            }
        
        # Try to get existing user by email (in case uid changed)
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        
        if user:
            # Update uid and last login
            cursor.execute('UPDATE users SET uid = ?, last_login = CURRENT_TIMESTAMP WHERE email = ?', (uid, email))
            conn.commit()
            return {
                'id': user[0],
                'uid': uid,  # Return updated uid
                'email': user[2],
                'role': user[3],
                'created_at': user[4],
                'last_login': user[5]
            }
        
        # Create new user if doesn't exist
        try:
            cursor.execute('''
                INSERT INTO users (uid, email, role)
                VALUES (?, ?, 'user')
            ''', (uid, email))
            conn.commit()
            
            # Get the newly created user
            cursor.execute('SELECT * FROM users WHERE uid = ?', (uid,))
            user = cursor.fetchone()
            return {
                'id': user[0],
                'uid': user[1],
                'email': user[2],
                'role': user[3],
                'created_at': user[4],
                'last_login': user[5]
            }
        except sqlite3.IntegrityError as e:
            logging.warning(f"User creation failed due to constraint, trying to fetch existing user: {e}")
            # If creation failed due to constraint, try to get existing user again
            cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
            user = cursor.fetchone()
            if user:
                return {
                    'id': user[0],
                    'uid': user[1],
                    'email': user[2],
                    'role': user[3],
                    'created_at': user[4],
                    'last_login': user[5]
                }
            return None
            
    except Exception as e:
        logging.error(f"Error managing user: {e}")
        return None
    finally:
        conn.close()

def set_user_role(email, role):
    """Set a user's role by email"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('UPDATE users SET role = ? WHERE email = ?', (role, email))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Error setting user role: {e}")
        return False
    finally:
        conn.close()

def get_user_role(uid):
    """Get user's role by uid"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT role FROM users WHERE uid = ?', (uid,))
        result = cursor.fetchone()
        return result[0] if result else 'user'
    except Exception as e:
        logging.error(f"Error getting user role: {e}")
        return 'user'
    finally:
        conn.close()

def require_auth(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for Firebase token in session or request headers
        auth_token = session.get('firebase_token') or request.headers.get('Authorization')
        
        if auth_token:
            decoded_token = verify_firebase_token(auth_token)
            if decoded_token:
                # Store user info in session for use in routes
                user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
                
                # Special handling for admin email (same as require_admin)
                if decoded_token.get('email') == 'danieljstrauss1@gmail.com':
                    if not user_info or user_info.get('role') != 'admin':
                        set_user_role('danieljstrauss1@gmail.com', 'admin')
                        user_info = get_or_create_user(decoded_token.get('uid'), 'danieljstrauss1@gmail.com')
                
                if user_info:
                    session['user_info'] = user_info
                    return f(*args, **kwargs)
        
        # If AJAX request, return JSON error
        if request.is_json or request.headers.get('Accept') == 'application/json':
            return jsonify({'error': 'Authentication required'}), 401
        # Otherwise redirect to login
        return redirect(url_for('login_page', return_url=request.url))
    
    return decorated_function

def require_admin(f):
    """Decorator to require admin role for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for Firebase token in session or request headers
        auth_token = session.get('firebase_token') or request.headers.get('Authorization')
        
        if auth_token:
            decoded_token = verify_firebase_token(auth_token)
            if decoded_token:
                # Get user info and check admin role
                user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
                
                # Temporary admin bypass for specific email
                user_email = decoded_token.get('email', '')
                if user_email == 'danieljstrauss1@gmail.com':
                    # Force create admin entry if it doesn't exist
                    if not user_info or user_info.get('role') != 'admin':
                        set_user_role(user_email, 'admin')
                        user_info = get_or_create_user(decoded_token.get('uid'), user_email)
                    
                    session['user_info'] = user_info
                    return f(*args, **kwargs)
                
                if user_info and user_info.get('role') == 'admin':
                    session['user_info'] = user_info
                    return f(*args, **kwargs)
                else:
                    # User is authenticated but not admin
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'error': 'Admin access required'}), 403
                    return render_template('403.html'), 403
        
        # If AJAX request, return JSON error
        if request.is_json or request.headers.get('Accept') == 'application/json':
            return jsonify({'error': 'Authentication required'}), 401
        # Otherwise redirect to login
        return redirect(url_for('login_page', return_url=request.url))
    
    return decorated_function

# Determine the current or most recent season dynamically if possible, otherwise default
# Using 2025 for current season data
CURRENT_SEASON = 2025 # Using 2025 for the current season

# Cache for storing player stats to reduce API calls
PLAYER_STATS_CACHE = {}
TEAM_ROSTER_CACHE = {}
SCHEDULE_CACHE = {}
CACHE_TIMEOUT = 300  # Cache timeout in seconds (5 minutes)
LAST_CACHE_CLEAR = time.time()
ENABLE_CACHE = True  # Flag to enable/disable caching for debugging

# Daily data cache - this will hold pre-fetched data
DAILY_DATA_CACHE = {
    'pitchers': None,
    'hitters': None,
    'schedule': None,
    'last_updated': None,
    'update_date': None
}

# Database setup for persistent daily data storage
DATABASE_PATH = os.environ.get('DATABASE_PATH', 'daily_mlb_data.sqlite')

def init_database():
    """Initialize the database with tables for daily data storage"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Create table for users and roles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            uid TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create table for daily cached data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_data (
            id INTEGER PRIMARY KEY,
            data_type TEXT NOT NULL,
            data_date TEXT NOT NULL,
            data_content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(data_type, data_date)
        )
    ''')
    
    # Create table for daily schedule
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_schedule (
            id INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            game_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_date)
        )
    ''')
    
    # Create table for daily pitchers
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_pitchers (
            id INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            pitcher_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_date)
        )
    ''')
    
    # Create table for daily hitters
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_hitters (
            id INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            hitter_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_date)
        )
    ''')
    
    # Create table for blog articles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blog_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            author TEXT DEFAULT 'MLB Analyst',
            tags TEXT,
            status TEXT DEFAULT 'published',
            slug TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def save_daily_data(data_type, data_date, data_content):
    """Save daily data to database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO daily_data (data_type, data_date, data_content)
            VALUES (?, ?, ?)
        ''', (data_type, data_date, json.dumps(data_content)))
        
        conn.commit()
        logger.info(f"Saved {data_type} data for {data_date}")
    except Exception as e:
        logger.error(f"Error saving {data_type} data: {e}")
        conn.rollback()
    finally:
        conn.close()

def load_daily_data(data_type, data_date):
    """Load daily data from database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT data_content FROM daily_data 
            WHERE data_type = ? AND data_date = ?
        ''', (data_type, data_date))
        
        result = cursor.fetchone()
        if result:
            return json.loads(result[0])
        return None
    except Exception as e:
        logger.error(f"Error loading {data_type} data: {e}")
        return None
    finally:
        conn.close()

def create_article(title, content, summary="", author="MLB Analyst", tags="", status="published"):
    """Create a new blog article"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Generate slug from title
        slug = re.sub(r'[^a-zA-Z0-9\s]', '', title.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        
        # Ensure slug is unique
        original_slug = slug
        counter = 1
        while True:
            cursor.execute('SELECT id FROM blog_articles WHERE slug = ?', (slug,))
            if not cursor.fetchone():
                break
            slug = f"{original_slug}-{counter}"
            counter += 1
        
        cursor.execute('''
            INSERT INTO blog_articles (title, content, summary, author, tags, status, slug, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (title, content, summary, author, tags, status, slug))
        
        article_id = cursor.lastrowid
        conn.commit()
        logger.info(f"Created new article: {title} (ID: {article_id})")
        return article_id
    except Exception as e:
        logger.error(f"Error creating article: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_articles(limit=10, offset=0, status="published"):
    """Get published articles with pagination"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, title, summary, author, tags, slug, created_at, updated_at
            FROM blog_articles 
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        ''', (status, limit, offset))
        
        articles = []
        for row in cursor.fetchall():
            articles.append({
                'id': row[0],
                'title': row[1],
                'summary': row[2],
                'author': row[3],
                'tags': row[4].split(',') if row[4] else [],
                'slug': row[5],
                'created_at': row[6],
                'updated_at': row[7]
            })
        
        return articles
    except Exception as e:
        logger.error(f"Error fetching articles: {e}")
        return []
    finally:
        conn.close()

def get_article_by_slug(slug):
    """Get a specific article by its slug"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, title, content, summary, author, tags, slug, created_at, updated_at, status
            FROM blog_articles 
            WHERE slug = ?
        ''', (slug,))
        
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'content': row[2],
                'summary': row[3],
                'author': row[4],
                'tags': row[5].split(',') if row[5] else [],
                'slug': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'status': row[9]
            }
        return None
    except Exception as e:
        logger.error(f"Error fetching article by slug: {e}")
        return None
    finally:
        conn.close()

def get_article_by_id(article_id):
    """Get a specific article by its ID"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, title, content, summary, author, tags, slug, created_at, updated_at, status
            FROM blog_articles 
            WHERE id = ?
        ''', (article_id,))
        
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'content': row[2],
                'summary': row[3],
                'author': row[4],
                'tags': row[5].split(',') if row[5] else [],
                'slug': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'status': row[9]
            }
        return None
    except Exception as e:
        logger.error(f"Error fetching article by ID: {e}")
        return None
    finally:
        conn.close()

def update_article(article_id, title=None, content=None, summary=None, author=None, tags=None, status=None):
    """Update an existing article"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Build dynamic update query
        updates = []
        params = []
        
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if summary is not None:
            updates.append("summary = ?")
            params.append(summary)
        if author is not None:
            updates.append("author = ?")
            params.append(author)
        if tags is not None:
            updates.append("tags = ?")
            params.append(tags)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(article_id)
            
            query = f"UPDATE blog_articles SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, params)
            
            conn.commit()
            logger.info(f"Updated article ID: {article_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating article: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def delete_article(article_id):
    """Delete an article"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM blog_articles WHERE id = ?', (article_id,))
        conn.commit()
        logger.info(f"Deleted article ID: {article_id}")
        return True
    except Exception as e:
        logger.error(f"Error deleting article: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def fetch_daily_pitchers_data(date_str):
    """Fetch and process all pitcher data for the day"""
    logger.info(f"Fetching pitcher data for {date_str}")
    
    try:
        schedule = statsapi.schedule(date=date_str, sportId=1)
        
        if not schedule:
            logger.warning(f"No games scheduled for {date_str}")
            return {'error': 'No games scheduled', 'pitchers_data': []}
        
        games_data = []
        
        for game in schedule:
            try:
                if not isinstance(game, dict):
                    continue

                # Get game status
                current_game_status_string = game.get('status')
                
                # Define relevant statuses for probable pitchers
                relevant_statuses_for_probable_pitchers = {"Scheduled", "Pre-Game", "Preview", "Live"}
                
                if current_game_status_string in relevant_statuses_for_probable_pitchers:
                    home_pitcher_name_from_schedule = game.get('home_probable_pitcher')
                    away_pitcher_name_from_schedule = game.get('away_probable_pitcher')
                    
                    home_team_name = game.get('home_name', "N/A")
                    away_team_name = game.get('away_name', "N/A")
                    home_team_id = game.get('home_id')
                    away_team_id = game.get('away_id')

                    pitchers_to_process = []
                    if home_pitcher_name_from_schedule:
                        pitchers_to_process.append({
                            "name_on_schedule": home_pitcher_name_from_schedule, 
                            "team_name": home_team_name, 
                            "opponent_name": away_team_name,
                            "opponent_id": away_team_id
                        })
                    if away_pitcher_name_from_schedule:
                        pitchers_to_process.append({
                            "name_on_schedule": away_pitcher_name_from_schedule, 
                            "team_name": away_team_name, 
                            "opponent_name": home_team_name,
                            "opponent_id": home_team_id
                        })

                    for p_detail in pitchers_to_process:
                        pitcher_display_name = p_detail["name_on_schedule"]
                        actual_player_id = None
                        pitcher_stats = {}

                        try:
                            # Look up player ID
                            player_lookup_results = statsapi.lookup_player(pitcher_display_name)
                            
                            if (player_lookup_results and isinstance(player_lookup_results, list) and 
                                len(player_lookup_results) > 0 and isinstance(player_lookup_results[0], dict) and 
                                player_lookup_results[0].get('id')):
                                actual_player_id = player_lookup_results[0]['id']
                                
                                # Fetch stats using the player ID
                                pitcher_stats = get_player_stats(actual_player_id, group="pitching", type="season")
                                
                                # Extract stats
                                batters_faced = pitcher_stats.get('battersFaced', "N/A")
                                home_runs_allowed = pitcher_stats.get('homeRuns', "N/A")
                                
                                games_data.append({
                                    'name': pitcher_display_name,
                                    'team': p_detail["team_name"],
                                    'opponent': p_detail["opponent_name"],
                                    'opponent_id': p_detail["opponent_id"],
                                    'batters_faced': batters_faced,
                                    'home_runs_allowed': home_runs_allowed
                                })
                            else:
                                games_data.append({
                                    'name': pitcher_display_name,
                                    'team': p_detail["team_name"],
                                    'opponent': p_detail["opponent_name"],
                                    'opponent_id': p_detail["opponent_id"],
                                    'batters_faced': "ID Lookup Error",
                                    'home_runs_allowed': "ID Lookup Error"
                                })

                        except Exception as e:
                            logger.error(f"Error processing pitcher '{pitcher_display_name}': {e}")
                            games_data.append({
                                'name': pitcher_display_name,
                                'team': p_detail["team_name"],
                                'opponent': p_detail["opponent_name"],
                                'opponent_id': p_detail["opponent_id"],
                                'batters_faced': "Fetch Error",
                                'home_runs_allowed': "Fetch Error"
                            })
                
            except Exception as e:
                logger.error(f"Error processing game {game.get('game_id', 'unknown')}: {e}")
                continue
        
        result = {'pitchers_data': games_data, 'total_games': len(games_data)}
        logger.info(f"Successfully fetched pitcher data for {len(games_data)} games")
        return result
        
    except Exception as e:
        logger.error(f"Error in fetch_daily_pitchers_data: {e}")
        return {'error': str(e), 'pitchers_data': []}

def fetch_home_run_odds():
    """Fetch today's home run odds from the external API"""
    odds_url = "https://djstrauss08.github.io/HomeRunOdds//api/v1/homerun-props.json"
    
    # Initialize the player_odds dictionary
    player_odds = {}
    
    try:
        logger.info("Fetching home run odds data")
        response = requests.get(odds_url, timeout=10)
        response.raise_for_status()
        
        odds_data = response.json()
        
        # Log games with odds available
        games_with_odds = []
        for game in odds_data.get('games', []):
            away_team = game.get('away_team')
            home_team = game.get('home_team')
            games_with_odds.append(f"{away_team} @ {home_team}")
            
            for player in game.get('players', []):
                if player.get('line') == 0.5:  # Only "To Hit HR" props
                    player_name = player.get('player_name')
                    consensus_odds = player.get('over_odds', {}).get('consensus')
                    sportsbook_count = player.get('sportsbook_count', 0)
                    
                    if consensus_odds and sportsbook_count > 0:
                        # Format odds with + sign for positive odds
                        if str(consensus_odds).isdigit():
                            formatted_odds = f"+{consensus_odds}"
                        else:
                            formatted_odds = str(consensus_odds)
                        
                        player_odds[player_name] = {
                            'odds': formatted_odds,
                            'raw_odds': consensus_odds,
                            'sportsbook_count': sportsbook_count
                        }
        
        logger.info(f"Odds available for {len(games_with_odds)} games: {', '.join(games_with_odds)}")
        logger.info(f"Total players with HR odds: {len(player_odds)}")
        
        return player_odds
        
    except Exception as e:
        logger.error(f"Error fetching home run odds: {e}")
        return {}

def fetch_daily_hitters_data():
    """Fetch and process all hitter data for the day"""
    logger.info("Fetching daily hitter data")
    
    try:
        # Fetch home run odds first
        odds_data = fetch_home_run_odds()
        
        # Get list of games with odds coverage for better user feedback
        games_with_odds = set()
        try:
            odds_url = "https://djstrauss08.github.io/HomeRunOdds//api/v1/homerun-props.json"
            response = requests.get(odds_url, timeout=10)
            if response.status_code == 200:
                odds_json = response.json()
                for game in odds_json.get('games', []):
                    away_team = game.get('away_team', '')
                    home_team = game.get('home_team', '')
                    games_with_odds.add(away_team)
                    games_with_odds.add(home_team)
        except Exception as e:
            logger.warning(f"Could not fetch games with odds coverage: {e}")
        
        # Fetch league leaders for home runs  
        leaders_raw = statsapi.league_leaders('homeRuns', statGroup='hitting', limit=50, season=CURRENT_SEASON, sportId=1)
        
        # Check if the response is a string (likely the formatted table)
        if isinstance(leaders_raw, str):
            leaders = parse_league_leaders_string(leaders_raw)
        else:
            leaders = leaders_raw
            
        if not leaders:
            logger.error("No leaders data found or parsing failed")
            return {'error': "No home run leaders data available", 'hitters_data': []}
        
        # Use the same date as pitchers_api
        today_str = get_mlb_today()
        schedule_today = statsapi.schedule(date=today_str, sportId=1)
        
        # Create mapping of player names to opponents for today's games
        player_name_to_opponent = {}  # player_name -> opponent_name
        
        if isinstance(schedule_today, list) and schedule_today:
            for game in schedule_today:
                if not isinstance(game, dict): 
                    continue
                home_team_id = game.get('home_id')
                away_team_id = game.get('away_id')
                home_team_name = game.get('home_name', "N/A")
                away_team_name = game.get('away_name', "N/A")

                # Get rosters and map players to opponents
                if home_team_id and away_team_id:
                    try:
                        # Get home team roster
                        home_roster = statsapi.roster(home_team_id, season=CURRENT_SEASON)
                        if isinstance(home_roster, str):
                            for line in home_roster.split('\n'):
                                if line.strip() and '#' in line:
                                    # Parse format like "#99  RF  Aaron Judge"
                                    parts = line.strip().split()
                                    if len(parts) >= 3:
                                        # Player name is everything after position
                                        player_name = ' '.join(parts[2:])
                                        player_name_to_opponent[player_name] = away_team_name
                        
                        # Get away team roster  
                        away_roster = statsapi.roster(away_team_id, season=CURRENT_SEASON)
                        if isinstance(away_roster, str):
                            for line in away_roster.split('\n'):
                                if line.strip() and '#' in line:
                                    # Parse format like "#99  RF  Aaron Judge"
                                    parts = line.strip().split()
                                    if len(parts) >= 3:
                                        # Player name is everything after position
                                        player_name = ' '.join(parts[2:])
                                        player_name_to_opponent[player_name] = home_team_name
                                        
                    except Exception as e:
                        logger.error(f"Error fetching rosters for game {home_team_name} vs {away_team_name}: {e}")
                        continue

        logger.info(f"Found {len(player_name_to_opponent)} players with games today")

        # Process each leader
        hitters_data = []
        for leader in leaders:
            try:
                player_id = leader.get('person_id')
                if not player_id:
                    continue
                
                player_name = leader.get('name', get_player_name(player_id))
                team_name = leader.get('team_name', "N/A")
                home_runs = leader.get('value')
                
                # Get at-bats from player stats
                player_stats = get_player_stats(player_id, group="hitting", type="season")
                at_bats = player_stats.get('atBats', "N/A")
                
                # Check if we have a matchup today by player name
                opponent_today = player_name_to_opponent.get(player_name, "No game today")
                
                # Get today's odds for this player
                player_odds = odds_data.get(player_name, {})
                odds_display = player_odds.get('odds', 'N/A')
                
                # Provide better context for why odds aren't available
                if odds_display == 'N/A' and opponent_today != "No game today":
                    # Player has a game but no odds - check if their team is in odds coverage
                    if team_name not in games_with_odds:
                        odds_display = "Game not covered"
                
                hitters_data.append({
                    'name': player_name,
                    'team': team_name,
                    'opponent_today': opponent_today,
                    'at_bats': at_bats,
                    'home_runs': home_runs,
                    'todays_odds': odds_display,
                    'odds_raw': player_odds.get('raw_odds'),
                    'sportsbook_count': player_odds.get('sportsbook_count', 0)
                })
            except Exception as e:
                logger.error(f"Error processing hitter: {e}")
                continue

        # Sort by home runs (descending)
        hitters_data.sort(key=lambda x: int(x.get('home_runs', 0)) if isinstance(x.get('home_runs'), (int, str)) and str(x.get('home_runs', 0)).isdigit() else 0, 
                         reverse=True)
        
        result = {
            'hitters_data': hitters_data[:50], 
            'total_hitters': len(hitters_data),
            'odds_coverage': {
                'games_with_odds': len(games_with_odds) // 2,  # Divide by 2 since we count both teams
                'teams_covered': list(games_with_odds)
            }
        }
        logger.info(f"Successfully fetched hitter data for {len(hitters_data)} players")
        logger.info(f"Odds coverage: {len(games_with_odds) // 2} games covered")
        return result
        
    except Exception as e:
        logger.error(f"Error in fetch_daily_hitters_data: {e}")
        return {'error': str(e), 'hitters_data': []}

def daily_data_update():
    """Main function to update all daily data at 3AM"""
    logger.info("Starting daily data update...")
    update_start_time = time.time()
    
    today_str = get_mlb_today()
    
    try:
        # Fetch pitcher data
        pitcher_data = fetch_daily_pitchers_data(today_str)
        save_daily_data('pitchers', today_str, pitcher_data)
        DAILY_DATA_CACHE['pitchers'] = pitcher_data
        
        # Fetch hitter data
        hitter_data = fetch_daily_hitters_data()
        save_daily_data('hitters', today_str, hitter_data)
        DAILY_DATA_CACHE['hitters'] = hitter_data
        
        # Fetch schedule data
        schedule_data = statsapi.schedule(date=today_str, sportId=1)
        save_daily_data('schedule', today_str, schedule_data)
        DAILY_DATA_CACHE['schedule'] = schedule_data
        
        # Update cache metadata
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        DAILY_DATA_CACHE['update_date'] = today_str
        
        update_time = time.time() - update_start_time
        logger.info(f"Daily data update completed successfully in {update_time:.2f} seconds")
        
        # Clear old data (keep last 7 days)
        cleanup_old_data()
        
    except Exception as e:
        logger.error(f"Error during daily data update: {e}")
        traceback.print_exc()

def cleanup_old_data():
    """Clean up old data from the database (older than 7 days)"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Calculate cutoff date (7 days ago)
        cutoff_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        # Delete old data
        cursor.execute("DELETE FROM daily_data WHERE data_date < ?", (cutoff_date,))
        deleted_rows = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        logger.info(f"Cleaned up {deleted_rows} old data records (older than {cutoff_date})")
    except Exception as e:
        logger.error(f"Error cleaning up old data: {e}")

# Popular teams to preload (most viewed teams)
POPULAR_TEAMS = [
    147,  # New York Yankees
    121,  # New York Mets  
    119,  # Los Angeles Dodgers
    117,  # Houston Astros
    111,  # Boston Red Sox
    108,  # Los Angeles Angels
    145,  # Chicago White Sox
    112,  # Chicago Cubs
    158,  # Milwaukee Brewers
    143   # Philadelphia Phillies
]

def preload_popular_teams():
    """Preload matchup data for popular teams in background"""
    logger.info("Starting background preload of popular teams...")
    
    for i, team_id in enumerate(POPULAR_TEAMS):
        try:
            cache_key = f"matchup_{team_id}_{CURRENT_SEASON}"
            cached_data = get_cached_data(TEAM_MATCHUP_CACHE, cache_key)
            
            if cached_data is None:
                logger.info(f"Preloading team {team_id}... ({i+1}/{len(POPULAR_TEAMS)})")
                # Make internal request to populate cache
                with app.test_client() as client:
                    response = client.get(f'/api/matchup_hitters/{team_id}')
                    if response.status_code == 200:
                        logger.info(f"Successfully preloaded team {team_id}")
                    else:
                        logger.warning(f"Failed to preload team {team_id}: {response.status_code}")
                
                # Add delay between requests to prevent overwhelming the system
                if i < len(POPULAR_TEAMS) - 1:  # Don't delay after the last team
                    time.sleep(2)  # 2 second delay between teams
            else:
                logger.info(f"Team {team_id} already cached")
                
        except Exception as e:
            logger.error(f"Error preloading team {team_id}: {e}")
            # Continue with next team instead of crashing
            continue
    
    logger.info("Completed background preload of popular teams")

def load_today_data_on_startup():
    """Load today's data from database on application startup"""
    today_str = get_mlb_today()
    
    logger.info("Loading today's data from database...")
    
    # Load pitcher data
    pitcher_data = load_daily_data('pitchers', today_str)
    if pitcher_data:
        DAILY_DATA_CACHE['pitchers'] = pitcher_data
        logger.info("Loaded pitcher data from database")
    
    # Load hitter data
    hitter_data = load_daily_data('hitters', today_str)
    if hitter_data:
        DAILY_DATA_CACHE['hitters'] = hitter_data
        logger.info("Loaded hitter data from database")
    
    # Load schedule data
    schedule_data = load_daily_data('schedule', today_str)
    if schedule_data:
        DAILY_DATA_CACHE['schedule'] = schedule_data
        logger.info("Loaded schedule data from database")
    
    if pitcher_data or hitter_data or schedule_data:
        DAILY_DATA_CACHE['update_date'] = today_str
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        logger.info("Successfully loaded today's data from database")
    else:
        logger.info("No cached data found for today, will fetch on first request")

def setup_scheduler():
    """Set up the background scheduler for daily updates and preloading"""
    global scheduler
    
    if scheduler is None:
        scheduler = BackgroundScheduler()
        
        # Schedule daily data update at 3 AM Eastern
        scheduler.add_job(
            func=daily_data_update,
            trigger="cron",
            hour=3,
            minute=0,
            timezone=pytz.timezone('US/Eastern'),
            id='daily_update',
            replace_existing=True
        )
        
        # Schedule popular teams preloading at 3:30 AM Eastern (after daily update)
        scheduler.add_job(
            func=preload_popular_teams,
            trigger="cron", 
            hour=3,
            minute=30,
            timezone=pytz.timezone('US/Eastern'),
            id='preload_teams',
            replace_existing=True
        )
        
        # Schedule cleanup at 2 AM Eastern (before daily update)
        scheduler.add_job(
            func=cleanup_old_data,
            trigger="cron",
            hour=2,
            minute=0,
            timezone=pytz.timezone('US/Eastern'),
            id='cleanup',
            replace_existing=True
        )
        
        try:
            scheduler.start()
            logger.info("Background scheduler started successfully")
            logger.info("Scheduled jobs:")
            logger.info("- Daily data update: 3:00 AM ET")
            logger.info("- Popular teams preload: 3:30 AM ET") 
            logger.info("- Data cleanup: 2:00 AM ET")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
    else:
        logger.info("Scheduler already running")

# Initialize database and scheduler on import
init_database()
load_today_data_on_startup()
scheduler = setup_scheduler()

# Cache data with timeout for automatic expiration
def cache_data(cache_dict, key, data, timeout=CACHE_TIMEOUT):
    if not ENABLE_CACHE:
        return data
    
    cache_dict[key] = {
        'data': data,
        'timestamp': time.time(),
        'expires': time.time() + timeout
    }
    return data

# Get data from cache if available and not expired
def get_cached_data(cache_dict, key):
    if not ENABLE_CACHE:
        return None
        
    if key in cache_dict:
        entry = cache_dict[key]
        if entry['expires'] > time.time():
            print(f"Cache hit for {key}")
            return entry['data']
        else:
            # Expired entry, remove it
            print(f"Cache expired for {key}")
            del cache_dict[key]
    return None
    
def get_player_stats(player_id, group="pitching", type="season"):
    """
    Gets player stats from cache or API with caching for better performance.
    
    Args:
        player_id: The player's ID
        group: The stats group (pitching/hitting)
        type: The type of stats (season/seasonAdvanced)
    
    Returns:
        Dictionary of parsed stats
    """
    # Create a cache key using player_id, group, and type
    cache_key = f"{player_id}_{group}_{type}"
    
    # Check if we have this data in the cache
    cached_stats = get_cached_data(PLAYER_STATS_CACHE, cache_key)
    if cached_stats is not None:
        return cached_stats
    
    # If not in cache, fetch from API
    print(f"Cache miss - Fetching stats for Player ID: {player_id}, group: {group}, type: {type}")
    stats_data = statsapi.player_stats(player_id, group=group, type=type)
    
    # Parse the stats data
    parsed_stats = {}
    if isinstance(stats_data, str):
        # Parse string response
        for line in stats_data.split('\n'):
            if ':' in line:
                key_value = line.split(':', 1)
                if len(key_value) == 2:
                    key = key_value[0].strip()
                    value = key_value[1].strip()
                    parsed_stats[key] = value
    elif isinstance(stats_data, dict):
        stats_list = stats_data.get('stats')
        if isinstance(stats_list, list) and stats_list:
            first_stat_entry = stats_list[0]
            if isinstance(first_stat_entry, dict) and isinstance(first_stat_entry.get('stats'), dict):
                parsed_stats = first_stat_entry['stats']
    
    # Store in cache and return
    return cache_data(PLAYER_STATS_CACHE, cache_key, parsed_stats)

def get_team_abbreviation_to_id_map():
    """
    Fetches all teams and creates a mapping from team abbreviation to team ID.
    This is useful for looking up team IDs when only abbreviation or name is known.
    """
    teams = statsapi.lookup_team('') # Search for all teams
    team_map = {}
    for team in teams:
        if team.get('abbreviation') and team.get('id'):
            team_map[team['abbreviation'].upper()] = team['id']
        # Fallback or alternative names if abbreviation isn't always present or consistent
        if team.get('teamName') and team.get('id'):
            team_map[team['teamName'].upper()] = team['id']
    return team_map

# Cache team maps for the entire session
TEAM_ABBREVIATION_TO_ID = get_team_abbreviation_to_id_map()
ID_TO_TEAM_ABBREVIATION = {v: k for k, v in TEAM_ABBREVIATION_TO_ID.items()}

@lru_cache(maxsize=100)
def get_player_name(player_id):
    try:
        player_info_list = statsapi.lookup_player(player_id)
        # Ensure player_info_list is a list and has content
        if isinstance(player_info_list, list) and player_info_list:
            player_data = player_info_list[0]
            # Ensure player_data is a dictionary
            if isinstance(player_data, dict):
                return player_data.get('fullName', f"Unknown Player (ID: {player_id})")
        
        # If checks fail, log and return a specific message
        print(f"Could not retrieve valid player name for ID {player_id}. API response: {player_info_list}")
        return f"Player Info N/A (ID: {player_id})"
    except Exception as e:
        print(f"Error in get_player_name for ID {player_id}: {e}")
        return f"Error Getting Name (ID: {player_id})"

def get_team_roster(team_id, season=CURRENT_SEASON):
    """Get team roster with caching"""
    cache_key = f"roster_{team_id}_{season}"
    
    # Check cache first
    cached_roster = get_cached_data(TEAM_ROSTER_CACHE, cache_key)
    if cached_roster is not None:
        return cached_roster
    
    # Fetch from API if not in cache
    roster_data = statsapi.roster(team_id, season=season)
    
    # Store in cache with longer timeout (rosters don't change often)
    return cache_data(TEAM_ROSTER_CACHE, cache_key, roster_data, timeout=3600)  # 1 hour cache

def get_daily_schedule(date_str=None):
    """Get schedule for a given date with caching"""
    if date_str is None:
        date_str = get_mlb_today()
    
    cache_key = f"schedule_{date_str}"
    
    # Check cache first
    cached_schedule = get_cached_data(SCHEDULE_CACHE, cache_key)
    if cached_schedule is not None:
        return cached_schedule
    
    # Fetch from API if not in cache
    schedule_data = statsapi.schedule(date=date_str, sportId=1)
    
    # Store in cache with a shorter timeout (schedule details can change)
    return cache_data(SCHEDULE_CACHE, cache_key, schedule_data, timeout=300)  # 5 minutes cache

@app.route('/')
def index():
    return render_template('index.html', season=CURRENT_SEASON)

@app.route('/pitchers')
def pitchers_page():
    return render_template('pitchers.html', today_date=get_mlb_today(), season=CURRENT_SEASON)

@app.route('/api/pitchers')
def pitchers_api():
    """Optimized pitchers API using pre-fetched daily data"""
    today_str = get_mlb_today()
    
    # First, try to get data from daily cache
    if (DAILY_DATA_CACHE['pitchers'] is not None and 
        DAILY_DATA_CACHE['update_date'] == today_str):
        logger.info("Serving pitcher data from daily cache")
        return jsonify({
            **DAILY_DATA_CACHE['pitchers'],
            'today_date': today_str,
            'source': 'daily_cache',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    
    # Try to load from database if not in memory cache
    db_data = load_daily_data('pitchers', today_str)
    if db_data:
        logger.info("Serving pitcher data from database")
        DAILY_DATA_CACHE['pitchers'] = db_data
        DAILY_DATA_CACHE['update_date'] = today_str
        return jsonify({
            **db_data,
            'today_date': today_str,
            'source': 'database',
            'last_updated': DAILY_DATA_CACHE.get('last_updated', 'unknown')
        })
    
    # Fallback to live fetching if no cached data available
    logger.warning("No cached pitcher data found, fetching live data")
    try:
        live_data = fetch_daily_pitchers_data(today_str)
        # Cache the live data for future requests
        DAILY_DATA_CACHE['pitchers'] = live_data
        DAILY_DATA_CACHE['update_date'] = today_str
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        
        # Also save to database
        save_daily_data('pitchers', today_str, live_data)
        
        return jsonify({
            **live_data,
            'today_date': today_str,
            'source': 'live_fallback',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    except Exception as e:
        logger.error(f"Error in live fallback for pitchers: {e}")
        return jsonify({
            'error': f"Unable to fetch pitcher data: {e}",
            'pitchers_data': [],
            'today_date': today_str,
            'source': 'error'
        })

@app.route('/hitters')
def hitters_page():
    return render_template('hitters.html', season=CURRENT_SEASON)

@app.route('/login')
def login_page():
    return render_template('login.html', season=CURRENT_SEASON)

@app.route('/api/auth/verify', methods=['POST'])
def verify_auth():
    """Verify Firebase token and store in session"""
    data = request.get_json()
    if not data or 'idToken' not in data:
        return jsonify({'error': 'ID token required'}), 400
    
    id_token = data['idToken']
    if verify_firebase_token(id_token):
        session['firebase_token'] = id_token
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Invalid token'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/user-info', methods=['GET'])
@require_auth
def get_user_info():
    """Get current user's information including role"""
    user_info = session.get('user_info')
    if user_info:
        return jsonify({
            'email': user_info.get('email'),
            'role': user_info.get('role'),
            'isAdmin': user_info.get('role') == 'admin'
        })
    return jsonify({'error': 'User not found'}), 404

@app.route('/debug/auth')
def debug_auth_info():
    """Debug route to show authentication details"""
    auth_token = session.get('firebase_token') or request.headers.get('Authorization')
    
    debug_info = {
        'has_auth_token': bool(auth_token),
        'session_keys': list(session.keys()),
        'user_info_in_session': session.get('user_info'),
    }
    
    if auth_token:
        decoded_token = verify_firebase_token(auth_token)
        debug_info['token_decoded'] = bool(decoded_token)
        if decoded_token:
            debug_info['token_data'] = {
                'uid': decoded_token.get('uid'),
                'email': decoded_token.get('email')
            }
            
            # Try to get user from database
            user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
            debug_info['user_from_db'] = user_info
    
    return jsonify(debug_info)

@app.route('/admin-debug')
def admin_debug_page():
    """Serve the admin debug page"""
    return send_from_directory('.', 'admin_debug.html')

@app.route('/api/admin/make-admin', methods=['POST'])
def make_admin():
    """Make a user admin - only for initial setup or by existing admins"""
    data = request.get_json()
    email = data.get('email')
    secret_key = data.get('secret_key')
    
    # Allow initial admin setup with secret key, or require existing admin
    if secret_key == 'initial_admin_setup_2025':
        # This is for initial setup only
        if set_user_role(email, 'admin'):
            return jsonify({'success': True, 'message': f'User {email} is now an admin'})
        else:
            return jsonify({'error': 'Failed to set admin role'}), 500
    else:
        # Check if current user is admin
        auth_token = session.get('firebase_token') or request.headers.get('Authorization')
        if auth_token:
            decoded_token = verify_firebase_token(auth_token)
            if decoded_token:
                user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
                if user_info and user_info.get('role') == 'admin':
                    if set_user_role(email, 'admin'):
                        return jsonify({'success': True, 'message': f'User {email} is now an admin'})
                    else:
                        return jsonify({'error': 'Failed to set admin role'}), 500
        
        return jsonify({'error': 'Admin access required'}), 403

@app.route('/admin')
@require_admin
def admin_page():
    return render_template('admin.html', season=CURRENT_SEASON)

@app.route('/profile')
@require_auth
def profile_page():
    return render_template('profile.html', season=CURRENT_SEASON)

@app.route('/blog')
def blog_page():
    page = request.args.get('page', 1, type=int)
    per_page = 5
    offset = (page - 1) * per_page
    
    articles = get_articles(limit=per_page, offset=offset)
    
    return render_template('blog.html', 
                         articles=articles, 
                         current_page=page,
                         has_next=len(articles) == per_page,
                         season=CURRENT_SEASON)

@app.route('/blog/<slug>')
def article_page(slug):
    article = get_article_by_slug(slug)
    if not article:
        return render_template('404.html'), 404
    
    return render_template('article.html', article=article, season=CURRENT_SEASON)

@app.route('/admin/articles')
@require_admin
def admin_articles():
    articles = get_articles(limit=50, status="published") + get_articles(limit=50, status="draft")
    return render_template('admin_articles.html', articles=articles, season=CURRENT_SEASON)

@app.route('/admin/articles/new')
@require_admin
def new_article():
    return render_template('edit_article.html', article=None, season=CURRENT_SEASON)

@app.route('/admin/articles/<int:article_id>/edit')
@require_admin
def edit_article_page(article_id):
    article = get_article_by_id(article_id)
    if not article:
        return render_template('404.html'), 404
    return render_template('edit_article.html', article=article, season=CURRENT_SEASON)

@app.route('/api/articles', methods=['POST'])
@require_admin
def create_article_api():
    data = request.get_json()
    
    # Debug logging
    logger.info(f"Received article creation request")
    logger.info(f"Request data: {data}")
    logger.info(f"Content-Type: {request.content_type}")
    
    if not data or not data.get('title') or not data.get('content'):
        logger.warning(f"Missing required fields - title: {data.get('title') if data else 'No data'}, content: {data.get('content') if data else 'No data'}")
        return jsonify({'error': 'Title and content are required'}), 400
    
    article_id = create_article(
        title=data['title'],
        content=data['content'],
        summary=data.get('summary', ''),
        author=data.get('author', 'MLB Analyst'),
        tags=data.get('tags', ''),
        status=data.get('status', 'published')
    )
    
    if article_id:
        logger.info(f"Successfully created article with ID: {article_id}")
        return jsonify({'success': True, 'article_id': article_id})
    else:
        logger.error("Failed to create article in database")
        return jsonify({'error': 'Failed to create article'}), 500

@app.route('/api/articles/<int:article_id>', methods=['PUT'])
@require_admin
def update_article_api(article_id):
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    success = update_article(
        article_id=article_id,
        title=data.get('title'),
        content=data.get('content'),
        summary=data.get('summary'),
        author=data.get('author'),
        tags=data.get('tags'),
        status=data.get('status')
    )
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to update article'}), 500

@app.route('/api/articles/<int:article_id>', methods=['DELETE'])
@require_admin
def delete_article_api(article_id):
    success = delete_article(article_id)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to delete article'}), 500

# Upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload-image', methods=['POST'])
@require_admin
def upload_image():
    """Handle image uploads for blog articles"""
    try:
        # Check if the post request has the file part
        if 'image' not in request.files:
            return jsonify({'error': 'No image file provided'}), 400
        
        file = request.files['image']
        
        # If user does not select file, browser also submits an empty part without filename
        if file.filename == '':
            return jsonify({'error': 'No image file selected'}), 400
        
        # Validate file type
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, JPEG, GIF, WebP'}), 400
        
        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)  # Reset file pointer
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': 'File size too large. Maximum 5MB allowed'}), 400
        
        if file and allowed_file(file.filename):
            # Generate unique filename
            file_extension = file.filename.rsplit('.', 1)[1].lower()
            unique_filename = f"{uuid.uuid4().hex}.{file_extension}"
            
            # Save file
            file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
            file.save(file_path)
            
            # Return the URL that can be used to access the image
            image_url = f"/static/uploads/{unique_filename}"
            
            logger.info(f"Image uploaded successfully: {unique_filename}")
            
            return jsonify({
                'success': True,
                'url': image_url,
                'filename': unique_filename,
                'size': file_size
            })
        
        return jsonify({'error': 'File upload failed'}), 500
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}")
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500

@app.route('/api/hitters')
def hitters_api():
    """Optimized hitters API using pre-fetched daily data"""
    today_str = get_mlb_today()
    
    # First, try to get data from daily cache
    if (DAILY_DATA_CACHE['hitters'] is not None and 
        DAILY_DATA_CACHE['update_date'] == today_str):
        logger.info("Serving hitter data from daily cache")
        return jsonify({
            **DAILY_DATA_CACHE['hitters'],
            'today_date': today_str,
            'source': 'daily_cache',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    
    # Try to load from database if not in memory cache
    db_data = load_daily_data('hitters', today_str)
    if db_data:
        logger.info("Serving hitter data from database")
        DAILY_DATA_CACHE['hitters'] = db_data
        DAILY_DATA_CACHE['update_date'] = today_str
        return jsonify({
            **db_data,
            'today_date': today_str,
            'source': 'database',
            'last_updated': DAILY_DATA_CACHE.get('last_updated', 'unknown')
        })
    
    # Fallback to live fetching if no cached data available
    logger.warning("No cached hitter data found, fetching live data")
    try:
        live_data = fetch_daily_hitters_data()
        # Cache the live data for future requests
        DAILY_DATA_CACHE['hitters'] = live_data
        DAILY_DATA_CACHE['update_date'] = today_str
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        
        # Also save to database
        save_daily_data('hitters', today_str, live_data)
        
        return jsonify({
            **live_data,
            'today_date': today_str,
            'source': 'live_fallback',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    except Exception as e:
        logger.error(f"Error in live fallback for hitters: {e}")
        return jsonify({
            'error': f"Unable to fetch hitter data: {e}",
            'hitters_data': [],
            'today_date': today_str,
            'source': 'error'
        })

def parse_league_leaders_string(leaders_str):
    """Parse the league leaders string response into structured data."""
    results = []
    
    try:
        # Skip header lines
        lines = leaders_str.strip().split('\n')
        
        # Find the line that starts with 'Rank'
        header_index = -1
        for i, line in enumerate(lines):
            if line.startswith('Rank'):
                header_index = i
                break
        
        if header_index == -1:
            print("Could not find header line in league leaders string")
            return results
        
        # Process each line after the header
        for line in lines[header_index+1:]:
            if not line.strip():
                continue
            
            # Parse fixed-width format: Rank (4 chars), Name (20 chars), Team (24 chars), Value (rest)
            try:
                if len(line) < 50:  # Skip short lines
                    continue
                    
                rank = line[:4].strip()
                name = line[4:24].strip()
                team = line[24:48].strip()
                value = line[48:].strip()
                
                if not rank or not name or not value:
                    continue
                
                # Look up player_id
                player_info = statsapi.lookup_player(name)
                player_id = player_info[0]['id'] if player_info and len(player_info) > 0 else None
                
                results.append({
                    'rank': rank,
                    'name': name,
                    'team_name': team,
                    'person_id': player_id,
                    'value': value
                })
            except Exception as e:
                print(f"Error parsing line: {line}. Error: {e}")
                continue
                
    except Exception as e:
        print(f"Error parsing league leaders string: {e}")
        traceback_str = traceback.format_exc()
        print(f"Traceback: {traceback_str}")
    
    return results

@app.route('/matchup_hitters/<int:team_id>')
def matchup_hitters_page(team_id):
    team_name = "Unknown Team"
    try:
        team_info_list = statsapi.lookup_team(team_id)
        if isinstance(team_info_list, list) and team_info_list and isinstance(team_info_list[0], dict):
            team_name = team_info_list[0].get('name', f"Team ID {team_id}")
    except Exception:
        team_name = f"Team ID {team_id}"
    
    return render_template('matchup_hitters.html', team_name=team_name, team_id=team_id, season=CURRENT_SEASON)

# Add team matchup cache at the top level
TEAM_MATCHUP_CACHE = {}

@app.route('/api/matchup_hitters/<int:team_id>')
def matchup_hitters_api(team_id):
    """Optimized matchup hitters API with improved caching and performance"""
    
    # Check if we have cached data for this team (cache for 1 hour)
    cache_key = f"matchup_{team_id}_{CURRENT_SEASON}"
    cached_data = get_cached_data(TEAM_MATCHUP_CACHE, cache_key)
    if cached_data is not None:
        logger.info(f"Serving cached matchup data for team {team_id}")
        return jsonify(cached_data)
    
    logger.info(f"Fetching fresh matchup data for team {team_id}")
    start_time = time.time()
    
    matchup_data = []
    team_name = "Unknown Team"
    
    try:
        # Fetch home run odds data first
        odds_data = fetch_home_run_odds()
        logger.info(f"Fetched odds for {len(odds_data)} players")
        
        # Get team info with caching
        team_info_cache_key = f"team_info_{team_id}"
        team_info = get_cached_data(TEAM_ROSTER_CACHE, team_info_cache_key)
        
        if team_info is None:
            team_info_list = statsapi.lookup_team(team_id)
            if isinstance(team_info_list, list) and team_info_list and isinstance(team_info_list[0], dict):
                team_info = team_info_list[0]
                cache_data(TEAM_ROSTER_CACHE, team_info_cache_key, team_info, timeout=3600)  # 1 hour cache
            else:
                logger.error(f"Could not find team info for team {team_id}")
                return jsonify({'error': f"Could not find team: {team_id}", 'matchup_data': [], 'team_name': f"Team ID {team_id}"})
        
        team_name = team_info.get('name', f"Team ID {team_id}")
        
        # Get roster with improved caching
        roster_data = get_team_roster(team_id)
        
        if not isinstance(roster_data, str) or "Error" in roster_data:
            logger.error(f"Error fetching roster for team {team_id}: {roster_data}")
            return jsonify({'error': "Could not fetch team roster", 'matchup_data': [], 'team_name': team_name})
        
        # Parse roster more efficiently - extract all player names first
        player_names = []
        for line in roster_data.split('\n'):
            if line.strip() and '#' in line:
                try:
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        player_name = ' '.join(parts[2:])
                        player_names.append(player_name)
                except Exception as e:
                    logger.warning(f"Error parsing roster line: {line}. Error: {e}")
                    continue
        
        logger.info(f"Found {len(player_names)} players on roster for team {team_id}")
        
        # Batch lookup player IDs with caching
        player_id_map = {}  # name -> id
        for player_name in player_names:
            # Check cache first
            player_cache_key = f"player_lookup_{player_name.replace(' ', '_')}"
            cached_player_id = get_cached_data(PLAYER_STATS_CACHE, player_cache_key)
            
            if cached_player_id is not None:
                player_id_map[player_name] = cached_player_id
            else:
                try:
                    player_lookup = statsapi.lookup_player(player_name)
                    if player_lookup and isinstance(player_lookup, list) and player_lookup:
                        player_id = player_lookup[0]['id']
                        player_id_map[player_name] = player_id
                        # Cache the lookup result for 24 hours
                        cache_data(PLAYER_STATS_CACHE, player_cache_key, player_id, timeout=86400)
                    else:
                        logger.warning(f"Could not find ID for player {player_name}")
                except Exception as e:
                    logger.error(f"Error looking up player {player_name}: {e}")
                    continue
        
        logger.info(f"Successfully mapped {len(player_id_map)} player IDs")
        
        # Fetch stats for all players with improved error handling
        player_details_list = []
        for player_name, player_id in player_id_map.items():
            try:
                # Use existing caching for player stats
                s_stats = get_player_stats(player_id, group="hitting", type="season")
                adv_stats = get_player_stats(player_id, group="hitting", type="seasonAdvanced")
                
                # Validate and process stats
                at_bats = s_stats.get('atBats', 0)
                if isinstance(at_bats, str) and at_bats.isdigit():
                    at_bats = int(at_bats)
                elif not isinstance(at_bats, int):
                    at_bats = 0
                
                # Only include players with meaningful at-bats
                if at_bats > 10:  # Increased threshold for better data quality
                    home_runs = s_stats.get('homeRuns', 0)
                    if isinstance(home_runs, str) and home_runs.isdigit():
                        home_runs = int(home_runs)
                    elif not isinstance(home_runs, int):
                        home_runs = 0
                    
                    # Get today's odds for this player
                    player_odds = odds_data.get(player_name, {})
                    odds_display = player_odds.get('odds', 'N/A')
                    
                    player_details_list.append({
                        'name': player_name,  # Use cached name instead of lookup
                        'at_bats': at_bats,
                        'home_runs': home_runs,
                        'ops': s_stats.get('ops', "N/A"),
                        'iso': adv_stats.get('iso', "N/A"),
                        'babip': adv_stats.get('babip', "N/A"),
                        'hr_pa': adv_stats.get('homeRunsPerPlateAppearance', "N/A"),
                        'todays_odds': odds_display,
                        'odds_raw': player_odds.get('raw_odds'),
                        'sportsbook_count': player_odds.get('sportsbook_count', 0)
                    })
                    
            except Exception as e:
                logger.error(f"Error fetching stats for player {player_name} (ID: {player_id}): {e}")
                continue
        
        # Sort by home runs and take top 10
        matchup_data = sorted(player_details_list, 
                            key=lambda x: x['home_runs'] if isinstance(x['home_runs'], int) else 0, 
                            reverse=True)[:10]
        
        if not matchup_data:
            result = {'error': "No hitter data available for this team.", 'matchup_data': [], 'team_name': team_name}
        else:
            # Add summary stats for odds availability
            players_with_odds = sum(1 for player in matchup_data if player['todays_odds'] != 'N/A')
            result = {
                'matchup_data': matchup_data, 
                'team_name': team_name,
                'odds_summary': {
                    'players_with_odds': players_with_odds,
                    'total_players': len(matchup_data)
                }
            }
        
        # Cache the result for 1 hour
        cache_data(TEAM_MATCHUP_CACHE, cache_key, result, timeout=3600)
        
        elapsed_time = time.time() - start_time
        logger.info(f"Matchup data for team {team_id} fetched in {elapsed_time:.2f} seconds")
        
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error fetching matchup hitters data for team ID {team_id}: {e}")
        return jsonify({'error': f"Error fetching data: {e}", 'matchup_data': [], 'team_name': team_name})

# Add a route for application config information
@app.route('/api/config')
def app_config():
    return jsonify({
        'season': CURRENT_SEASON,
        'cache_enabled': ENABLE_CACHE,
        'cache_timeout': CACHE_TIMEOUT
    })

@app.route('/api/daily_status')
def daily_status():
    """Get the status of daily data updates"""
    today_str = get_mlb_today()
    current_et = get_eastern_time()
    
    status = {
        'today': today_str,
        'eastern_time': {
            'current_time': current_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
            'timezone': str(current_et.tzinfo),
            'hour': current_et.hour,
            'next_changeover': '3:00 AM ET tomorrow' if current_et.hour >= 3 else f'3:00 AM ET today ({3 - current_et.hour} hours)'
        },
        'daily_cache_status': {
            'pitchers': DAILY_DATA_CACHE['pitchers'] is not None,
            'hitters': DAILY_DATA_CACHE['hitters'] is not None,
            'schedule': DAILY_DATA_CACHE['schedule'] is not None,
            'last_updated': DAILY_DATA_CACHE.get('last_updated'),
            'update_date': DAILY_DATA_CACHE.get('update_date')
        },
        'database_status': {
            'pitchers': load_daily_data('pitchers', today_str) is not None,
            'hitters': load_daily_data('hitters', today_str) is not None,
            'schedule': load_daily_data('schedule', today_str) is not None
        },
        'scheduler_running': scheduler.running if scheduler is not None else False,
        'next_scheduled_update': '3:00 AM Eastern Time daily'
    }
    
    return jsonify(status)

@app.route('/api/trigger_update')
def trigger_update():
    """Manually trigger a daily data update"""
    try:
        # Run the update in a separate thread to avoid timeout
        import threading
        thread = threading.Thread(target=daily_data_update)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Daily data update triggered successfully',
            'timestamp': get_eastern_time().isoformat(),
            'mlb_date': get_mlb_today()
        })
    except Exception as e:
        logger.error(f"Error triggering update: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Error triggering update: {e}',
            'timestamp': get_eastern_time().isoformat(),
            'mlb_date': get_mlb_today()
        })

@app.route('/api/clear_cache')
def clear_cache_endpoint():
    # Only clear cache if authorized
    try:
        # Clear all caches
        PLAYER_STATS_CACHE.clear()
        TEAM_ROSTER_CACHE.clear()
        TEAM_MATCHUP_CACHE.clear()
        
        logger.info("All caches cleared successfully")
        return jsonify({'success': True, 'message': 'All caches cleared successfully'})
    except Exception as e:
        logger.error(f"Error clearing caches: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/clear_matchup_cache')
def clear_matchup_cache():
    """Clear only the matchup cache for faster testing"""
    try:
        TEAM_MATCHUP_CACHE.clear()
        logger.info("Matchup cache cleared successfully")
        return jsonify({'success': True, 'message': 'Matchup cache cleared successfully'})
    except Exception as e:
        logger.error(f"Error clearing matchup cache: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/preload_teams')
def preload_teams_endpoint():
    """Manually trigger preloading of popular teams"""
    try:
        import threading
        thread = threading.Thread(target=preload_popular_teams)
        thread.daemon = True
        thread.start()
        
        return jsonify({'success': True, 'message': 'Preloading started in background'})
    except Exception as e:
        logger.error(f"Error starting preload: {e}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080) 