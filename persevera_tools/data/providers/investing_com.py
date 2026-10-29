from typing import Optional, List, Dict
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime

from .base import DataProvider, DataRetrievalError


class InvestingComProvider(DataProvider):
    """Provider for Investing.com data, such as the economic calendar."""

    def __init__(self):
        super().__init__()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://br.investing.com/economic-calendar",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        }
        self.base_url = "https://br.investing.com/economic-calendar/Service/getCalendarFilteredData"
        self.timeout = 30  # seconds; without it a stalled connection hangs forever
        # Flag keys that differ from the country names used in the output
        self.flag_to_country_map = {'Europe': 'Eurozone'}
        self.currency_to_country_map = {
            'USD': 'United States', 'BRL': 'Brazil', 'EUR': 'Eurozone',
            'GBP': 'United Kingdom', 'JPY': 'Japan', 'CAD': 'Canada',
            'AUD': 'Australia', 'CHF': 'Switzerland', 'CNY': 'China', 'MXN': 'Mexico'
        }

    def _get_country_from_currency(self, currency: str) -> str:
        """Map currency codes to country names"""
        currency_clean = currency.strip().upper()
        return self.currency_to_country_map.get(currency_clean, 'Unknown')

    def _get_country(self, flag_cell, currency: str) -> str:
        """Country from the row's flag (e.g. data-img_key="United_States"); falls back to the currency map"""
        flag = flag_cell.find('span', class_='ceFlags')
        flag_key = flag.get('data-img_key', '') if flag else ''
        if flag_key:
            return self.flag_to_country_map.get(flag_key, flag_key.replace('_', ' '))
        return self._get_country_from_currency(currency)

    def _parse_volatility(self, volatility_html: str) -> str:
        """Parse volatility level from bull icons"""
        bull_count = volatility_html.count('grayFullBullishIcon')
        if bull_count == 1:
            return "Low"
        elif bull_count == 2:
            return "Moderate"
        elif bull_count == 3:
            return "High"
        return "Unknown"

    def _fetch_economic_calendar(self, countries: List[str], time_filter: str, current_tab: str) -> Optional[Dict]:
        """Fetches economic calendar data from Investing.com."""
        data = {
            "country[]": countries,
            "timeZone": "12", 
            "timeFilter": time_filter,
            "currentTab": current_tab,
            "limit_from": "0"
        }
        
        try:
            response = requests.post(self.base_url, headers=self.headers, data=data, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {e}")
            raise DataRetrievalError(f"Failed to retrieve data from Investing.com: {e}") from e

    def _parse_economic_calendar(self, html_content: str) -> pd.DataFrame:
        """Convert the investing.com API response to a pandas DataFrame"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        events = []
        current_date = None
        
        rows = soup.find_all('tr')
        
        for row in rows:
            if 'theDay' in row.get('class', []):
                current_date = row.get_text(strip=True)
                continue
            
            if 'js-event-item' in row.get('class', []):
                try:
                    cells = row.find_all('td')
                    
                    if len(cells) >= 7:
                        time = cells[0].get_text(strip=True)
                        currency = cells[1].get_text(strip=True)
                        volatility = self._parse_volatility(str(cells[2]))
                        event_cell = cells[3]
                        event_link = event_cell.find('a')
                        if event_link:
                            event_name = event_link.get_text(strip=True)
                            event_url = event_link.get('href', '')
                        else:
                            event_name = event_cell.get_text(strip=True)
                            event_url = ''
                        
                        actual = cells[4].get_text(strip=True) or None
                        forecast = cells[5].get_text(strip=True) or None
                        previous = cells[6].get_text(strip=True) or None
                        
                        event_datetime = row.get('data-event-datetime', '')
                        # event_attr_id identifies the event type (e.g. 829 = Manufacturing PMI, every month);
                        # the row id (eventRowId_559178) identifies this occurrence and survives reschedules
                        event_id = row.get('event_attr_id', '')
                        occurrence_id = row.get('id', '').removeprefix('eventRowId_')

                        country = self._get_country(cells[1], currency)

                        events.append({
                            'date_header': current_date, 'time': time, 'datetime': event_datetime,
                            'country': country, 'currency': currency, 'volatility': volatility,
                            'event_name': event_name, 'actual': actual, 'forecast': forecast,
                            'previous': previous, 'occurrence_id': occurrence_id, 'event_id': event_id,
                            'event_url': event_url
                        })
                        
                except Exception as e:
                    self.logger.warning(f"Error parsing row: {e}")
                    continue
        
        df = pd.DataFrame(events)
        
        if not df.empty:
            df['datetime_parsed'] = pd.to_datetime(df['datetime'], format='%Y/%m/%d %H:%M:%S', errors='coerce')
            df['currency'] = df['currency'].str.strip()
            df['event_name'] = df['event_name'].str.strip()
            df = df.sort_values('datetime_parsed').reset_index(drop=True)
        
        return df

    def get_data(self, category: str, data_type: str = 'economic_calendar', **kwargs) -> pd.DataFrame:
        """
        Retrieve economic calendar data from Investing.com.
        
        Args:
            category (str): The category of data to retrieve. Only 'economic_calendar' is supported.
            countries (list, optional): List of country codes. Defaults to ['5', '32'] (Brazil, USA).
            time_filter (str, optional): Time filter. Defaults to 'timeRemain'.
            current_tab (str, optional): Data tab. Defaults to 'nextWeek'.
            
        Returns:
            pd.DataFrame: DataFrame with columns: ['date', 'occurrence_id', 'event_id', 'country', 'currency',
                'event_name', 'importance', 'event_url']. 'date' is in Brasília time (timeZone 12);
                'occurrence_id' is unique per release, 'event_id' is the recurring event type.
        """
        self._log_processing(category)
        if data_type != 'economic_calendar':
            raise NotImplementedError(f"Data type '{data_type}' not supported by InvestingComProvider.")

        countries = kwargs.get('countries', ['5', '32'])
        time_filter = kwargs.get('time_filter', 'timeRemain')
        current_tab = kwargs.get('current_tab', 'nextWeek')

        json_response = self._fetch_economic_calendar(countries, time_filter, current_tab)
        if not json_response or 'data' not in json_response:
            raise DataRetrievalError("No data in response from Investing.com")

        df = self._parse_economic_calendar(json_response['data'])
        if df.empty:
            self.logger.warning("No events found after parsing.")
            return pd.DataFrame()

        df = df[['datetime_parsed', 'occurrence_id', 'event_id','country', 'currency', 'event_name', 'volatility', 'event_url']]
        df = df.rename(columns={'datetime_parsed': 'date', 'volatility': 'importance'})
        
        return df
