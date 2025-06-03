import statsapi
import json

# Francisco Lindor's player ID
player_id = 596019  # Francisco Lindor

print("Testing available stats for a batter (Francisco Lindor)...")

# Get season stats
season_stats = statsapi.player_stats(player_id, group="hitting", type="season")
print("\n=== SEASON STATS ===")
if isinstance(season_stats, dict):
    print(json.dumps(season_stats, indent=2))
elif isinstance(season_stats, str):
    print(season_stats)
else:
    print(f"Unexpected type: {type(season_stats)}")

# Get career stats
career_stats = statsapi.player_stats(player_id, group="hitting", type="career")
print("\n=== CAREER STATS ===")
if isinstance(career_stats, dict):
    print(json.dumps(career_stats, indent=2))
elif isinstance(career_stats, str):
    print(career_stats)
else:
    print(f"Unexpected type: {type(career_stats)}")

# Get advanced stats
advanced_stats = statsapi.player_stats(player_id, group="hitting", type="seasonAdvanced")
print("\n=== ADVANCED STATS ===")
if isinstance(advanced_stats, dict):
    print(json.dumps(advanced_stats, indent=2))
elif isinstance(advanced_stats, str):
    print(advanced_stats)
else:
    print(f"Unexpected type: {type(advanced_stats)}")

# Try to get Statcast data if available
statcast_stats = statsapi.player_stats(player_id, group="hitting", type="statcast")
print("\n=== STATCAST STATS (may not be available) ===")
if isinstance(statcast_stats, dict):
    print(json.dumps(statcast_stats, indent=2))
elif isinstance(statcast_stats, str):
    print(statcast_stats)
else:
    print(f"Unexpected type: {type(statcast_stats)}")

# Check if there are other hitting stat types
print("\n=== TESTING OTHER STAT TYPES ===")
stat_types = ["yearByYear", "yearByYearAdvanced", "rankings", "gameLog"]
for stat_type in stat_types:
    print(f"\nTrying stat type: {stat_type}")
    try:
        other_stats = statsapi.player_stats(player_id, group="hitting", type=stat_type)
        if isinstance(other_stats, dict):
            print(json.dumps(other_stats, indent=2)[:1000] + "..." if len(json.dumps(other_stats)) > 1000 else json.dumps(other_stats, indent=2))
        elif isinstance(other_stats, str):
            print(other_stats[:1000] + "..." if len(other_stats) > 1000 else other_stats)
        else:
            print(f"Unexpected type: {type(other_stats)}")
    except Exception as e:
        print(f"Error getting {stat_type}: {e}") 