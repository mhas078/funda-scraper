from funda_scraper import FundaScraper
import asyncio

scraper = FundaScraper(area='%5B"gemeente-lopik,50km"%5D',
   want_to="buy",
   page_start=1,
   n_pages=10,
   min_price=500000,
   max_price=1000000,
   min_floor_area=175,
   min_perceel_area=250)

loop = asyncio.get_event_loop()
loop.run_until_complete(scraper.run(raw_data=True))