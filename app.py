import time
import pandas as pd
from fredapi import Fred 
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for
import yfinance as yf 

# --- Configuration ---
# !!! REPLACE WITH YOUR ACTUAL FRED KEY !!!
FRED_API_KEY = "42370c22b0b74d122ae7fb953cd217f7" 

# How long the data is valid for (24 hours in seconds)
CACHE_LIFETIME_SECONDS = 86400 

# --- Financial Constants ---
SWR = 0.04  # Safe Withdrawal Rate (4%)
FI_MULTIPLIER = 1 / SWR # 25x Annual Expenses
MAX_HISTORICAL_YEARS = 53 # Limit based on reliable long-term data lookback

# Global Cache Object (REVISED to hold period-specific data)
CACHE = {
    'inflation_data': None,   
    'market_data': None,      
    'timestamp': 0,           
    'status': 'Awaiting Fetch',
    'cache_periods': {} # NEW: Stores rates keyed by the lookback period (e.g., '15_20')
}

# --- Sample Inputs (for initial GET request and fallback) ---
SAMPLE_INPUTS = {
    'current_age': 30,
    'retirement_age': 65,
    'years_to_contribute': 15,
    'annual_spending': 120000.00,
    'current_pv': 50000.00,
}

app = Flask(__name__)


# --- Core Utility Functions ---

def is_cache_expired():
    """Checks if the global cached data is older than the CACHE_LIFETIME_SECONDS."""
    current_time = time.time()
    data_is_empty = CACHE['inflation_data'] is None or CACHE['market_data'] is None
    return (current_time - CACHE['timestamp']) > CACHE_LIFETIME_SECONDS or data_is_empty

def update_cache(inflation_data, market_data):
    """Updates the cache with new metadata and the current timestamp."""
    global CACHE
    CACHE['inflation_data'] = inflation_data
    CACHE['market_data'] = market_data
    CACHE['timestamp'] = time.time()
    CACHE['status'] = f"Cached on {datetime.fromtimestamp(CACHE['timestamp']).strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"Cache updated. Status: {CACHE['status']}")

def calculate_cagr(series):
    """
    Calculates the Compound Annual Growth Rate (CAGR) from a pandas Series.
    """
    if series.empty or len(series) < 2:
        return None

    start_value = series.iloc[0]
    end_value = series.iloc[-1]
    
    start_date = series.index[0]
    end_date = series.index[-1]
    
    time_span_years = (end_date - start_date).days / 365.25
    
    cagr = (end_value / start_value) ** (1 / time_span_years) - 1
    
    return cagr


# --- Dynamic Rate Fetching Functions ---

def fetch_fred_rate(start_date):
    """Fetches CPI data from FRED and calculates the CAGR from the start_date."""
    try:
        fred = Fred(api_key=FRED_API_KEY)
        cpi_data = fred.get_series('CPIAUCSL', observation_start=start_date).dropna()
        long_term_inflation_rate = calculate_cagr(cpi_data)
        
        return float(long_term_inflation_rate)
    except Exception as e:
        print(f"Error fetching FRED data for period starting {start_date}: {e}")
        return 0.03 # Fallback

def fetch_market_rate(start_date):
    """Fetches S&P 500 data from yfinance and calculates the CAGR from the start_date."""
    try:
        symbol = '^GSPC' 
        market_data_yf = yf.download(symbol, start=start_date, end=datetime.now(), progress=False)
        
        price_series = market_data_yf['Close'].dropna()
        long_term_nominal_return = calculate_cagr(price_series) 
        
        return float(long_term_nominal_return)
    except Exception as e:
        print(f"Error fetching Yahoo Finance data for period starting {start_date}: {e}")
        return 0.10 # Fallback


def fetch_all_api_data(k, N_Coast):
    """
    Handles fetching and processing of all four required rates, using dynamic cache keying.
    
    This function checks two things: 
    1. If the entire cache is stale (older than 24 hours).
    2. If the cache contains data for the specific lookback period requested (k and N_Coast).
    """
    # --- 1. Define Cache Key and Check Cache ---
    k = max(1, k)
    N_Coast = max(1, N_Coast)
    
    # Create a unique key for the requested time horizon (e.g., '15_20')
    cache_key = f"{k}_{N_Coast}"
    
    # Check if cache is fresh in time
    if not is_cache_expired():
        # Check if the specific period requested is already calculated
        if cache_key in CACHE['cache_periods']:
            print(f"Cache is fresh and contains data for periods {cache_key}. Returning cached data.")
            cached_data = CACHE['cache_periods'][cache_key]
            # Return cached rates
            return cached_data['RN_C'], cached_data['I_C'], cached_data['RN_G'], cached_data['I_G']
        
        print(f"Cache time is fresh, but required period {cache_key} is missing. Calculating new rates...")
    else:
        # If time is expired, reset period cache
        CACHE['cache_periods'] = {}
        print("Cache expired. Clearing period cache and fetching all new data...")

    # --- 2. Calculate Historical Lookback Dates ---
    today = datetime.now()
    
    start_date_contrib = (today - pd.DateOffset(years=k)).strftime('%Y-%m-%d')
    start_date_coast = (today - pd.DateOffset(years=N_Coast)).strftime('%Y-%m-%d')
    
    # --- 3. Fetch all four rates dynamically ---
    RN_C = fetch_market_rate(start_date_contrib)
    I_C = fetch_fred_rate(start_date_contrib)
    
    RN_G = fetch_market_rate(start_date_coast)
    I_G = fetch_fred_rate(start_date_coast)
    
    # --- 4. Store New Data in Period Cache and Update Global Status ---
    
    new_period_data = {
        'RN_C': RN_C, 'I_C': I_C, 
        'RN_G': RN_G, 'I_G': I_G
    }
    
    # Store the result for this new lookback period
    CACHE['cache_periods'][cache_key] = new_period_data
    
    # Update global cache metadata
    inflation_data = {
        'I_C': I_C, 'I_G': I_G,
        'latest_cpi_date': datetime.now().strftime('%Y-%m-%d'), 
    }
    market_data = {
        'RN_C': RN_C, 'RN_G': RN_G,
        'source': 'Yahoo Finance (^GSPC)',
    }
    
    update_cache(inflation_data, market_data)
    
    return RN_C, I_C, RN_G, I_G


# --- Flask Application Routes ---

@app.route('/', methods=['GET', 'POST'])
def index():
    """
    Main route to process user input, run the Coast FIRE calculation, and render the page.
    """
    
    # --- 1. Handle User Input and Validation ---
    
    user_inputs = SAMPLE_INPUTS
    validation_error = None
    
    if request.method == 'POST':
        try:
            submitted_inputs = {
                'current_age': int(request.form.get('current_age')),
                'retirement_age': int(request.form.get('retirement_age')),
                'years_to_contribute': int(request.form.get('years_to_contribute')),
                'annual_spending': float(request.form.get('annual_spending')),
                'current_pv': float(request.form.get('current_pv')),
            }
            
            # Check 1: Retirement must be after contribution ends
            if submitted_inputs['retirement_age'] < (submitted_inputs['current_age'] + submitted_inputs['years_to_contribute']):
                validation_error = "Retirement Age must be after the Contribution Period ends."
            
            # Check 2: Max lookback limit (53 years)
            total_years = submitted_inputs['retirement_age'] - submitted_inputs['current_age']
            if total_years > MAX_HISTORICAL_YEARS:
                validation_error = f"The total time horizon ({total_years} years) exceeds the maximum data lookback available."
            
            if validation_error is None:
                user_inputs = submitted_inputs
            
        except:
            print("Warning: Invalid form data received. Using sample inputs.")
            validation_error = "Please ensure all number fields are filled correctly."

    # Extract inputs (use validated or sample)
    AGE_C = user_inputs['current_age']
    AGE_R = user_inputs['retirement_age']
    k = user_inputs['years_to_contribute'] 
    PV = user_inputs['current_pv'] 
    SPEND = user_inputs['annual_spending'] 

    # Calculate Time Periods (used for display, even if invalid)
    N = AGE_R - AGE_C 
    N_Coast = N - k 

    if validation_error:
        # If error, bypass API and complex calculation, use hardcoded rates and set PMT to zero
        RN_C, I_C, RN_G, I_G = 0.10, 0.03, 0.10, 0.03 
        RRR_C = ((1 + RN_C) / (1 + I_C)) - 1; RRR_G = ((1 + RN_G) / (1 + I_G)) - 1
        fi_target = SPEND * FI_MULTIPLIER
        required_annual_pmt = 0.00
        fv_contrib_target = 0.00
        fv_pmt_target = 0.00
        time_to_coast = 0
    else:
        # --- 2. Fetch Dynamic Rates ---
        RN_C, I_C, RN_G, I_G = fetch_all_api_data(k, N_Coast)

        # --- 3. Calculate Real Rates ---
        # 
        RRR_C = ((1 + RN_C) / (1 + I_C)) - 1 if I_C > 0 else (RN_C - I_C)
        RRR_G = ((1 + RN_G) / (1 + I_G)) - 1 if I_G > 0 else (RN_G - I_G)

        # --- 4. Run the Coast FIRE Calculation ---
        # 
        fi_target = SPEND * FI_MULTIPLIER 
        fv_contrib_target = fi_target / ((1 + RRR_G) ** N_Coast) 
        fv_pv = PV * ((1 + RRR_C) ** k) 
        fv_pmt_target = fv_contrib_target - fv_pv 

        if fv_pmt_target <= 0:
            required_annual_pmt = 0.00
            time_to_coast = 0
        else:
            # Handle RRR_C = 0 to prevent DivisionByZeroError
            if RRR_C == 0:
                 annuity_factor = k
            else:
                 annuity_factor = (((1 + RRR_C) ** k) - 1) / RRR_C
                 
            required_annual_pmt = fv_pmt_target / annuity_factor
            time_to_coast = k
    
    # --- 5. Prepare Context for Template ---

    context = {
        # Rates and Assumptions
        'SWR': f"{SWR * 100:.0f}%",
        'cache_status': CACHE['status'],
        'market_source': CACHE['market_data'].get('source', 'N/A'),
        'cpi_date': CACHE['inflation_data'].get('latest_cpi_date', 'N/A'),
        'validation_error': validation_error,
        
        # Contribution Period Rates
        'RN_C': f"{RN_C * 100:.2f}%",
        'I_C': f"{I_C * 100:.2f}%",
        'RRR_C': f"{RRR_C * 100:.2f}%",
        
        # Growth Period Rates
        'RN_G': f"{RN_G * 100:.2f}%",
        'I_G': f"{I_G * 100:.2f}%",
        'RRR_G': f"{RRR_G * 100:.2f}%",
        
        # User Inputs (contains clean numbers for input value attributes)
        'input_data': user_inputs,
        
        # Financial Results (Formatted: $XX,XXX.XX)
        'fi_target': f"${fi_target:,.2f}",
        'coast_fire_target': f"${fv_contrib_target:,.2f}",
        'annual_contribution_required': f"${required_annual_pmt:,.2f}",
        
        # Other Results
        'age_coast_at': AGE_C + k,
        'years_coasting': N_Coast,
        'gap_to_coast': f"${max(0, fv_pmt_target):,.2f}", 
        'time_to_coast_estimate': f"{time_to_coast} years",
        'is_post': request.method == 'POST' and validation_error is None
    }

    # Print for console testing
    print(f"\n--- FINAL CALCULATION RESULTS ---")
    for key, value in context.items():
        if not key.startswith('input_data'):
            print(f"{key.ljust(30)}: {value}")
    print("-----------------------------------\n")

    return render_template('index.html', context=context)


# --- Main Run Block ---

def initialize_cache():
    """Ensures the cache is populated with either fresh data or safe fallbacks."""
    global CACHE
    if CACHE['inflation_data'] is None:
        CACHE['inflation_data'] = {'I_C': 0.03, 'I_G': 0.03, 'latest_cpi_date': 'N/A (Fallback)'}
    if CACHE['market_data'] is None:
        CACHE['market_data'] = {'RN_C': 0.10, 'RN_G': 0.10, 'source': 'N/A (Fallback)'}
    
    fetch_all_api_data(
        SAMPLE_INPUTS['years_to_contribute'], 
        SAMPLE_INPUTS['retirement_age'] - SAMPLE_INPUTS['current_age'] - SAMPLE_INPUTS['years_to_contribute']
    )

if __name__ == '__main__':
    initialize_cache() 
    app.run(debug=True)