"""Main funda scraper module"""
import argparse
import datetime
import json
import multiprocessing as mp
import os
from typing import List, Literal, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from tqdm.contrib.concurrent import process_map

from funda_scraper.config.core import config
from funda_scraper.preprocess import clean_list_date, preprocess_data
from funda_scraper.utils import logger
import asyncio
import aiohttp


class FundaScraper(object):
  """
    Handles the main scraping function.
    """

  def __init__(
      self,
      area: str,
      want_to: str,
      page_start: int = 1,
      n_pages: int = 1,
      find_past: bool = False,
      min_price: Optional[int] = None,
      max_price: Optional[int] = None,
      min_floor_area: Optional[int] = None,
      min_perceel_area: Optional[int] = None,
  ):
    # Init attributes
    self.area = area.lower().replace(" ", "-")
    self.want_to = want_to
    self.find_past = find_past
    self.page_start = max(page_start, 1)
    self.n_pages = max(n_pages, 1)
    self.page_end = self.page_start + self.n_pages - 1
    self.min_price = min_price
    self.max_price = max_price
    self.min_floor_area = min_floor_area
    self.min_perceel_area = min_perceel_area

    # Instantiate along the way
    self.links: List[str] = []
    self.raw_df = pd.DataFrame()
    self.clean_df = pd.DataFrame()
    self.base_url = config.base_url
    self.selectors = config.css_selector

  def __repr__(self):
    return (f"FundaScraper(area={self.area}, "
            f"want_to={self.want_to}, "
            f"n_pages={self.n_pages}, "
            f"page_start={self.page_start}, "
            f"find_past={self.find_past})"
            f"min_price={self.min_price})"
            f"max_price={self.max_price})")

  @property
  def to_buy(self) -> bool:
    """Whether to buy or not"""
    if self.want_to.lower() in ["buy", "koop", "b", "k"]:
      return True
    elif self.want_to.lower() in ["rent", "huur", "r", "h"]:
      return False
    else:
      raise ValueError("'want_to' must be either 'buy' or 'rent'.")

  @staticmethod
  def _check_dir() -> None:
    """Check whether a temporary directory for data"""
    if not os.path.exists("data"):
      os.makedirs("data")

  async def get_links_from_all_parent(self, urls: List[str]) -> List[str]:
    """Scrape all the available housing items from one Funda search page."""
    # Fetch all urls
    responses = await self.fetch_all(urls)
    response_texts = [response[1] for response in responses]
    return self.process_parent_links(response_texts)

  def process_parent_links(self, response_texts: List[str]) -> List[str]:
    all_children = []
    for text in response_texts:
      soup = BeautifulSoup(text, "lxml")
      script_tags = soup.find_all("script", {"type": "application/ld+json"})
      if len(script_tags) == 0:
        print(text)

      script_tag = soup.find_all("script", {"type": "application/ld+json"})[0]
      json_data = json.loads(script_tag.contents[0])
      all_children += [item["url"] for item in json_data["itemListElement"]]

    return list(set(all_children))

  def reset(
      self,
      area: Optional[str] = None,
      want_to: Optional[str] = None,
      page_start: Optional[int] = None,
      n_pages: Optional[int] = None,
      find_past: Optional[bool] = None,
      min_price: Optional[int] = None,
      max_price: Optional[int] = None,
  ) -> None:
    """Overwrite or initialise the searching scope."""
    if area is not None:
      self.area = area
    if want_to is not None:
      self.want_to = want_to
    if page_start is not None:
      self.page_start = max(page_start, 1)
    if n_pages is not None:
      self.n_pages = max(n_pages, 1)
    if find_past is not None:
      self.find_past = find_past
    if min_price is not None:
      self.min_price = min_price
    if max_price is not None:
      self.max_price = max_price

  async def fetch_all_links(self,
                            page_start: int = None,
                            n_pages: int = None) -> None:
    """Find all the available links across multiple pages."""

    page_start = self.page_start if page_start is None else page_start
    n_pages = self.n_pages if n_pages is None else n_pages

    logger.info(
        "*** Phase 1: Fetch all the available links from all pages *** ")
    main_url = self._build_main_query_url()

    urls = [
        f"{main_url}&search_result={i}"
        for i in tqdm(range(page_start, page_start + n_pages))
    ]
    # Save urls to file
    child_urls = await self.get_links_from_all_parent(urls)
    # Save child_urls to file
    print(child_urls)

    urls = self.flatten(child_urls)
    urls = list(set(urls))
    logger.info(
        f"*** Got all the urls. {len(urls)} houses found from {self.page_start} to {self.page_end} ***"
    )
    self.links = urls

  def _build_main_query_url(self) -> str:
    query = "koop" if self.to_buy else "huur"
    main_url = f"{self.base_url}/zoeken/{query}?selected_area=%22{self.area}%22"

    if self.find_past:
      main_url = f"{main_url}&availability=%22unavailable%22"

    if self.min_price is not None or self.max_price is not None:
      min_price = "" if self.min_price is None else self.min_price
      max_price = "" if self.max_price is None else self.max_price
      main_url = f"{main_url}&price=%22{min_price}-{max_price}%22"

    if self.min_floor_area is not None:
      main_url = f"{main_url}&floor_area=%22{self.min_floor_area}-%22"

    if self.min_perceel_area is not None:
      main_url = f"{main_url}&plot_area=%22{self.min_perceel_area}-%22"

    return main_url

  @staticmethod
  def get_value_from_css(soup: BeautifulSoup, selector: str) -> str:
    """Use CSS selector to find certain features."""
    result = soup.select(selector)
    if len(result) > 0:
      result = result[0].text
    else:
      result = "na"
    return result

  async def fetch(self, session, url):
    await asyncio.sleep(1)
    async with session.get(url) as response:
      if response.status != 200:
        response.raise_for_status()
      text = await response.text()
      return [url, text]

  async def fetch_all(self, urls):
    tasks = []
    async with aiohttp.ClientSession(headers=config.header) as session:
      for url in urls:
        task = asyncio.create_task(self.fetch(session, url))
        tasks.append(task)
      results = await asyncio.gather(*tasks)
      return results

  async def download_one_link(self, link: str) -> List[str]:
    """Scrape all the features from one house item given a link."""

    # Initialize for each page
    loop = asyncio.get_event_loop()
    logger.info("*** Phase 2: Getting one link ***")
    future = loop.run_in_executor(
        None, lambda x: requests.get(x, headers=config.header), link)
    response = await future
    return response.text

  def process_one_link(self, response_text) -> List[str]:
    # logger.info("*** Phase 2: Received response ***")
    soup = BeautifulSoup(response_text, "lxml")

    # Get the value according to respective CSS selectors
    if self.to_buy:
      if self.find_past:
        list_since_selector = self.selectors.date_list
      else:
        list_since_selector = self.selectors.listed_since
    else:
      if self.find_past:
        list_since_selector = ".fd-align-items-center:nth-child(9) span"
      else:
        list_since_selector = ".fd-align-items-center:nth-child(7) span"

    result = [
        self.get_value_from_css(soup, self.selectors.price),
        self.get_value_from_css(soup, self.selectors.address),
        self.get_value_from_css(soup, self.selectors.descrip),
        self.get_value_from_css(soup, list_since_selector),
        self.get_value_from_css(soup, self.selectors.zip_code),
        self.get_value_from_css(soup, self.selectors.size),
        self.get_value_from_css(soup, self.selectors.year),
        self.get_value_from_css(soup, self.selectors.living_area),
        self.get_value_from_css(soup, self.selectors.kind_of_house),
        self.get_value_from_css(soup, self.selectors.building_type),
        self.get_value_from_css(soup, self.selectors.num_of_rooms),
        self.get_value_from_css(soup, self.selectors.num_of_bathrooms),
        self.get_value_from_css(soup, self.selectors.layout),
        self.get_value_from_css(soup, self.selectors.energy_label),
        self.get_value_from_css(soup, self.selectors.insulation),
        self.get_value_from_css(soup, self.selectors.heating),
        self.get_value_from_css(soup, self.selectors.ownership),
        self.get_value_from_css(soup, self.selectors.exteriors),
        self.get_value_from_css(soup, self.selectors.parking),
        self.get_value_from_css(soup, self.selectors.neighborhood_name),
        self.get_value_from_css(soup, self.selectors.date_list),
        self.get_value_from_css(soup, self.selectors.date_sold),
        self.get_value_from_css(soup, self.selectors.term),
        self.get_value_from_css(soup, self.selectors.price_sold),
        self.get_value_from_css(soup, self.selectors.last_ask_price),
        self.get_value_from_css(
            soup, self.selectors.last_ask_price_m2).split("\r")[0],
    ]

    # Deal with list_since_selector especially, since its CSS varies sometimes
    # if clean_list_date(result[4]) == "na":
    #     for i in range(6, 16):
    #         selector = f".fd-align-items-center:nth-child({i}) span"
    #         update_list_since = self.get_value_from_css(soup, selector)
    #         if clean_list_date(update_list_since) == "na":
    #             pass
    #         else:
    #             result[4] = update_list_since

    photos_list = [
        p.get("data-lazy-srcset") for p in soup.select(self.selectors.photo)
    ]
    photos_string = ", ".join(photos_list)

    # Clean up the retried result from one page
    result = [r.replace("\n", "").replace("\r", "").strip() for r in result]
    result.append(photos_string)
    # logger.info("*** Phase 2: processed response ***")
    return result

  def flatten(self, xss):
    return [x for xs in xss for x in xs]

  async def scrape_pages(self) -> None:
    """Scrape all the content acoss multiple pages."""

    logger.info("*** Phase 2: Start scraping from individual links ***")
    df = pd.DataFrame({key: [] for key in self.selectors.keys()})

    contents = await self.fetch_all(self.links)
    # contents = await asyncio.gather(
    #     *[self.download_one_link(link) for link in self.links])
    contents = [
        self.flatten([[page[0]], self.process_one_link(page[1])])
        for page in contents
    ]
    logger.info(
        "*** Phase 2: Downloaded all the links, now processing them ***")
    for i, c in enumerate(contents):
      df.loc[len(df)] = c

    df["city"] = df["url"].map(lambda x: x.split("/")[4])
    df["log_id"] = datetime.datetime.now().strftime("%Y%m-%d%H-%M%S")
    if not self.find_past:
      df = df.drop(["term", "price_sold", "date_sold"], axis=1)
    logger.info(f"*** All scraping done: {df.shape[0]} results ***")
    self.raw_df = df

  def save_csv(self, df: pd.DataFrame, filepath: str = None) -> None:
    """Save the result to a .csv file."""
    if filepath is None:
      self._check_dir()
      date = str(datetime.datetime.now().date()).replace("-", "")
      status = "unavailable" if self.find_past else "unavailable"
      want_to = "buy" if self.to_buy else "rent"
      filepath = f"./data/houseprice_{date}_{self.area}_{want_to}_{status}_{len(self.links)}.csv"
    df.to_csv(filepath, index=False)
    logger.info(f"*** File saved: {filepath}. ***")

  async def run(self,
                raw_data: bool = False,
                save: bool = False,
                filepath: str = None) -> pd.DataFrame:
    """
        Scrape all links and all content.

        :param raw_data: if true, the data won't be pre-processed
        :param save: if true, the data will be saved as a csv file
        :param filepath: the name for the file
        :return: the (pre-processed) dataframe from scraping
        """
    await self.fetch_all_links()
    await self.scrape_pages()

    if raw_data:
      df = self.raw_df
    else:
      logger.info("*** Cleaning data ***")
      df = preprocess_data(df=self.raw_df, is_past=self.find_past)
      self.clean_df = df

    if save:
      self.save_csv(df, filepath)

    logger.info("*** Done! ***")
    return df


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument(
      "--area",
      type=str,
      help="Specify which area you are looking for",
      default="amsterdam",
  )
  parser.add_argument(
      "--want_to",
      type=str,
      help="Specify you want to 'rent' or 'buy'",
      default="rent",
  )
  parser.add_argument(
      "--find_past",
      type=bool,
      help="Indicate whether you want to use hisotrical data or not",
      default=False,
  )
  parser.add_argument("--page_start",
                      type=int,
                      help="Specify which page to start scraping",
                      default=1)
  parser.add_argument("--n_pages",
                      type=int,
                      help="Specify how many pages to scrape",
                      default=1)
  parser.add_argument("--min_price",
                      type=int,
                      help="Specify the min price",
                      default=None)
  parser.add_argument("--max_price",
                      type=int,
                      help="Specify the max price",
                      default=None)
  parser.add_argument(
      "--raw_data",
      type=bool,
      help=
      "Indicate whether you want the raw scraping result or preprocessed one",
      default=False,
  )
  parser.add_argument(
      "--save",
      type=bool,
      help="Indicate whether you want to save the data or not",
      default=True,
  )

  args = parser.parse_args()
  scraper = FundaScraper(
      area=args.area,
      want_to=args.want_to,
      find_past=args.find_past,
      page_start=args.page_start,
      n_pages=args.n_pages,
      min_price=args.min_price,
      max_price=args.max_price,
  )
  df = scraper.run(raw_data=args.raw_data, save=args.save)
  print(df.head())
