"""
Resource Search Service
Uses YouTube API, Tavily, and SerpApi to fetch real learning resources.

Search strategy:
- YouTube API is used ONLY for video resources
- Tavily is the main search provider for non-YouTube resources
- SerpApi is used as fallback if Tavily is unavailable or returns no results

This version is:
- level-aware
- time-aware
- week-context-aware
- prefers:
    1) one YouTube
    2) one trusted non-YouTube
    3) one practice/project
    4) one extra best match (non-YouTube preferred, YouTube only if no other option)
- filters weak / noisy / unrelated results more aggressively
"""

import logging
import re
from typing import List, Dict, Any, Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class ResourceSearchService:
    # Trusted YouTube channels (educational, high quality)
    TRUSTED_YOUTUBE_CHANNELS = {
        "freeCodeCamp.org", "Traversy Media", "The Net Ninja", "Programming with Mosh",
        "Web Dev Simplified", "Fireship", "Academind", "CoreyMS Coding",
        "Real Python", "Tech With Tim", "Derek Banas", "sentdex",
        "3Blue1Brown", "StatQuest", "Data School", "CodingEntrepreneurs",
        "FreeCodeCamp Official", "JavaScript Mastery", "Andre Neubauer",
        "OpenJS Foundation", "TC39", "WesbosTV"
    }

    # Difficulty levels for resources
    DIFFICULTY_KEYWORDS = {
        "beginner": ["beginner", "basics", "fundamentals", "getting started", "introduction", "101", "crash course"],
        "beginner+": ["intermediate beginner", "beyond basics", "next steps"],
        "intermediate": ["intermediate", "intermediate advanced", "medium", "practical", "real-world"],
        "advanced": ["advanced", "expert", "mastery", "optimization", "patterns", "architecture", "case study", "best practices"]
    }

    # Clickbait and low-quality patterns
    CLICKBAIT_PATTERNS = [
        r"shorts?",
        r"in \d+ (seconds?|mins?|minute)",
        r"top \d+",
        r"everything in \d+ (hour|min)",
        r"vs ",
        r"\?\?\?",
        r"(shocking|amazing|incredible|mind-blowing|nobody knows)",
        r"don't (forget|miss)",
        r"must watch",
        r"\[ASMR\]",
    ]

    def __init__(self):
        self.youtube_api_key = getattr(settings, "YOUTUBE_API_KEY", "")
        self.serpapi_api_key = getattr(settings, "SERPAPI_API_KEY", "")
        self.tavily_api_key = getattr(settings, "TAVILY_API_KEY", "")

        self.youtube_base_url = "https://www.googleapis.com/youtube/v3/search"
        self.serpapi_base_url = "https://serpapi.com/search"
        self.tavily_base_url = "https://api.tavily.com/search"

    def _detect_difficulty(self, text: str) -> str:
        """Classify difficulty level from text (title + snippet)."""
        text_lower = text.lower()
        
        # Check in order of specificity
        for level in ["advanced", "intermediate", "beginner+", "beginner"]:
            keywords = self.DIFFICULTY_KEYWORDS.get(level, [])
            if any(kw in text_lower for kw in keywords):
                return level
        
        return "intermediate"  # Default

    def _estimate_duration(self, title: str, snippet: str, resource_type: str) -> str:
        """Estimate study time from resource metadata."""
        text = f"{title} {snippet}".lower()
        
        # YouTube-specific patterns
        if resource_type == "youtube":
            if any(p in text for p in ["shorts", "in 60 sec", "in 100 sec", "in 1 min"]):
                return "10 min"
            if any(p in text for p in ["in 10 min", "in 15 min", "in 30 min"]):
                return "30 min"
            if any(p in text for p in ["full course", "complete tutorial", "full playlist", "masterclass"]):
                return "full course"
            # Default YouTube is 30 min - 2 hours
            return "2 hours"
        
        # Docs/Reference
        if resource_type == "docs":
            return "30 min"  # Quick reference
        
        # Practice/Exercises
        if resource_type == "practice":
            if "beginner" in text:
                return "30 min"
            return "2 hours"  # Practice usually takes time
        
        # Project
        if resource_type == "project":
            return "full course"  # Projects are long-term
        
        # Course/Article
        if resource_type == "course":
            return "full course"
        if resource_type == "article":
            return "30 min"
        
        return "2 hours"  # Default

    def _is_clickbait_youtube(self, title: str, description: str) -> bool:
        """Detect clickbait/low-quality YouTube videos."""
        text = f"{title} {description}".lower()
        
        for pattern in self.CLICKBAIT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        
        return False

    def _is_trusted_channel(self, channel_title: str) -> bool:
        """Check if YouTube channel is in trusted list."""
        return channel_title in self.TRUSTED_YOUTUBE_CHANNELS

    def _semantic_distance(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity (0-1) between two texts."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0

    async def search_resources(
        self,
        resource_queries: List[Dict[str, Any]],
        max_per_week: int = 4,
        current_level: Optional[str] = None,
        target_level: Optional[str] = None,
        available_hours_per_week: Optional[int] = None,
        week_number: Optional[int] = None,
        duration_weeks: Optional[int] = None,
        context_keywords: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Build a balanced weekly resource set.

        Output preference:
        - 1 YouTube
        - 1 trusted non-YouTube docs/course/article
        - 1 practice/project
        - 1 extra best match (non-YouTube preferred, YouTube only if no other option)
        """
        youtube_results: List[Dict[str, Any]] = []
        non_youtube_results: List[Dict[str, Any]] = []

        for item in resource_queries or []:
            query = (item.get("query") or "").strip()
            resource_type = (item.get("type") or "article").strip().lower()
            title = (item.get("title") or query or "Learning Resource").strip()

            if not query:
                continue

            try:
                if resource_type == "youtube":
                    results = await self._search_youtube(query=query, title=title)
                    youtube_results.extend(results)
                    
                    # If YouTube fails, try Tavily as fallback for video content
                    if not results:
                        logger.debug("YouTube unavailable, trying Tavily as fallback for '%s'", query)
                        tavily_results = await self._search_tavily(
                            query=query + " tutorial video",
                            resource_type="youtube",
                            title=title
                        )
                        if tavily_results:
                            non_youtube_results.extend(tavily_results)
                else:
                    results = await self._search_tavily(
                        query=query,
                        resource_type=resource_type,
                        title=title
                    )

                    if not results:
                        results = await self._search_serpapi(
                            query=query,
                            resource_type=resource_type,
                            title=title
                        )

                    if not results:
                        results = self._get_fallback_resources(
                            query=query,
                            resource_type=resource_type,
                            title=title
                        )

                    non_youtube_results.extend(results)

            except Exception as e:
                logger.debug("Resource search error for query='%s': %s", query, str(e))

                if resource_type != "youtube":
                    fallback = self._get_fallback_resources(
                        query=query,
                        resource_type=resource_type,
                        title=title
                    )
                    non_youtube_results.extend(fallback)

        rank_kwargs = dict(
            current_level=current_level,
            target_level=target_level,
            available_hours_per_week=available_hours_per_week,
            week_number=week_number,
            duration_weeks=duration_weeks,
            context_keywords=context_keywords,
        )

        youtube_ranked = self._rank_and_deduplicate(items=youtube_results, **rank_kwargs)
        non_youtube_ranked = self._rank_and_deduplicate(items=non_youtube_results, **rank_kwargs)

        final_results: List[Dict[str, Any]] = []
        used_urls: set = set()

        def try_add_first(
            items: List[Dict[str, Any]],
            preferred_types: Optional[List[str]] = None
        ) -> bool:
            for item in items:
                url = item.get("url")
                item_type = item.get("type")
                if not url or url in used_urls:
                    continue
                if preferred_types and item_type not in preferred_types:
                    continue
                final_results.append(item)
                used_urls.add(url)
                return True
            return False

        # 1) one YouTube
        try_add_first(youtube_ranked)

        # 2) one trusted explanatory non-youtube (docs/course/article)
        try_add_first(non_youtube_ranked, preferred_types=["docs", "course", "article"])

        # 3) one practice/project
        try_add_first(non_youtube_ranked, preferred_types=["practice", "project"])

        # 4) fill remaining slots — non-YouTube first, then YouTube as last resort
        remaining_slots = max_per_week - len(final_results)
        if remaining_slots > 0:
            for item in non_youtube_ranked:
                if len(final_results) >= max_per_week:
                    break
                url = item.get("url")
                if not url or url in used_urls:
                    continue
                final_results.append(item)
                used_urls.add(url)

        if len(final_results) < max_per_week:
            for item in youtube_ranked:
                if len(final_results) >= max_per_week:
                    break
                url = item.get("url")
                if not url or url in used_urls:
                    continue
                final_results.append(item)
                used_urls.add(url)

        return final_results[:max_per_week]

    async def _search_youtube(self, query: str, title: str) -> List[Dict[str, Any]]:
        if not self.youtube_api_key:
            logger.debug("YOUTUBE_API_KEY missing, skipping YouTube search")
            return []

        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 8,
            "key": self.youtube_api_key,
            "videoEmbeddable": "true",
            "safeSearch": "moderate",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self.youtube_base_url, params=params)
                
                # Handle 403 Forbidden gracefully (quota exceeded or API not enabled)
                if response.status_code == 403:
                    logger.debug(
                        "YouTube API 403 Forbidden (quota exceeded or API not enabled). "
                        "Falling back to Tavily/SerpApi for resource discovery."
                    )
                    return []
                
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.debug("YouTube API error (%s): %s", e.response.status_code, str(e))
            return []
        except Exception as e:
            logger.debug("YouTube search exception: %s", str(e))
            return []

        results = []
        for index, item in enumerate(data.get("items", []), start=1):
            video_id = item.get("id", {}).get("videoId")
            snippet = item.get("snippet", {})
            if not video_id:
                continue

            results.append({
                "title": snippet.get("title") or title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "type": "youtube",
                "snippet": snippet.get("description") or snippet.get("channelTitle"),
                "source": "YouTube",
                "position": index,
                "thumbnail": (
                    snippet.get("thumbnails", {})
                    .get("high", {})
                    .get("url")
                ),
                "channel": snippet.get("channelTitle", "Unknown"),
                "difficulty": self._detect_difficulty(snippet.get("title", "")),
                "duration": "2 hours",  # YouTube videos are typically 30m-2h
                "is_trusted_channel": self._is_trusted_channel(snippet.get("channelTitle", "")),
            })

        return results

    async def _search_tavily(
        self,
        query: str,
        resource_type: str,
        title: str
    ) -> List[Dict[str, Any]]:
        """
        Tavily is the main provider for non-YouTube resources.
        Falls back to SerpApi if Tavily is unavailable.
        """
        if not self.tavily_api_key:
            logger.debug("TAVILY_API_KEY not configured, will use SerpApi fallback")
            return []

        payload = {
            "query": self._build_tavily_query(query, resource_type),
            "topic": "general",
            "search_depth": "advanced",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "exclude_domains": [
                "youtube.com",
                "youtu.be"
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.tavily_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.tavily_base_url,
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.warning("Tavily search failed for '%s': %s", query, e)
            return []

        results = []
        for idx, item in enumerate(data.get("results", []), start=1):
            url = item.get("url")
            if not url:
                continue

            url_lower = url.lower()

            if "youtube.com" in url_lower or "youtu.be" in url_lower:
                continue

            title = item.get("title") or title
            snippet_text = item.get("content") or item.get("snippet", "")
            
            results.append({
                "title": title,
                "url": url,
                "type": resource_type,
                "snippet": snippet_text,
                "source": "Tavily",
                "position": idx,
                "thumbnail": None,
                "difficulty": self._detect_difficulty(f"{title} {snippet_text}"),
                "duration": self._estimate_duration(title, snippet_text, resource_type),
            })

        return results

    async def _search_serpapi(
        self,
        query: str,
        resource_type: str,
        title: str
    ) -> List[Dict[str, Any]]:
        """
        SerpApi is fallback for non-YouTube resources when Tavily is unavailable.
        If SerpApi is also unavailable, returns empty list (will use hardcoded fallback).
        """
        if not self.serpapi_api_key:
            logger.debug("SERPAPI_API_KEY not configured, will use hardcoded fallback resources")
            return []

        try:
            params = {
                "engine": "google",
                "q": self._build_google_query(query, resource_type),
                "api_key": self.serpapi_api_key,
                "hl": "en",
                "gl": "eg",
                "num": 8,
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.serpapi_base_url, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.debug("SerpApi search failed for query='%s': %s", query, e)
            return []

        results = []
        for item in data.get("organic_results", [])[:8]:
            link = item.get("link")
            if not link:
                continue

            link_lower = link.lower()

            if "youtube.com" in link_lower or "youtu.be" in link_lower:
                if resource_type != "youtube":
                    continue
                normalized_type = "youtube"
            else:
                normalized_type = resource_type

            title = item.get("title") or title
            snippet_text = item.get("snippet", "")
            
            results.append({
                "title": title,
                "url": link,
                "type": normalized_type,
                "snippet": snippet_text,
                "source": item.get("source") or "Google",
                "position": item.get("position", 999),
                "thumbnail": None,
                "difficulty": self._detect_difficulty(f"{title} {snippet_text}"),
                "duration": self._estimate_duration(title, snippet_text, normalized_type),
            })

        return results

    def _get_fallback_resources(
        self,
        query: str,
        resource_type: str,
        title: str
    ) -> List[Dict[str, Any]]:
        """
        Returns smart fallback resources when all search providers fail.
        Prioritizes trusted learning platforms with actual content.
        """
        query_encoded = query.replace(" ", "+").replace("&", "%26").lower()
        
        fallback_map = {
            "docs": [
                {
                    "title": f"{title} - Official Documentation",
                    "url": "https://developer.mozilla.org/search?q=" + query_encoded,
                    "source": "MDN"
                },
                {
                    "title": f"{title} - Python Official Docs",
                    "url": "https://docs.python.org/3/search.html?q=" + query_encoded,
                    "source": "Python.org"
                },
            ],
            "course": [
                {
                    "title": f"{title} - Structured Learning",
                    "url": "https://www.udemy.com/courses/search/?q=" + query_encoded,
                    "source": "Udemy"
                },
                {
                    "title": f"{title} - Free Course",
                    "url": "https://www.coursera.org/search?query=" + query_encoded,
                    "source": "Coursera"
                },
            ],
            "practice": [
                {
                    "title": f"{title} - Coding Exercises",
                    "url": "https://www.freecodecamp.org/learn/",
                    "source": "FreeCodeCamp"
                },
                {
                    "title": f"{title} - Practice Problems",
                    "url": "https://leetcode.com/problemset/all/?search=" + query_encoded,
                    "source": "LeetCode"
                },
            ],
            "project": [
                {
                    "title": f"{title} - Project Examples",
                    "url": "https://github.com/search?q=" + query_encoded + "&type=repositories",
                    "source": "GitHub"
                },
                {
                    "title": f"{title} - Open Source Projects",
                    "url": "https://github.com/topics/" + query_encoded.replace("+", "-"),
                    "source": "GitHub Topics"
                },
            ],
            "article": [
                {
                    "title": f"{title} - Technical Articles",
                    "url": "https://dev.to/search?q=" + query_encoded,
                    "source": "Dev.to"
                },
                {
                    "title": f"{title} - Medium Articles",
                    "url": "https://medium.com/search?q=" + query_encoded,
                    "source": "Medium"
                },
            ],
        }

        resources = fallback_map.get(resource_type, fallback_map["article"])
        
        results = []
        for idx, resource in enumerate(resources, start=1):
            results.append({
                "title": resource["title"],
                "url": resource["url"],
                "type": resource_type,
                "snippet": f"Recommended {resource_type} resource for learning {title}",
                "source": resource["source"],
                "position": idx,
                "thumbnail": None,
                "difficulty": "beginner",  # Fallback is always beginner-friendly
                "duration": self._estimate_duration(resource["title"], "", resource_type),
            })
        
        return results

    def _build_tavily_query(self, query: str, resource_type: str) -> str:
        """
        Build Tavily search query — more specific than before.
        The query already contains the subtopic from generation service.
        """
        if resource_type == "youtube":
            return f"{query} tutorial walkthrough"
        elif resource_type == "docs":
            return f"{query} official documentation reference"
        elif resource_type == "course":
            return f"{query} structured course learning path curriculum"
        elif resource_type == "practice":
            return f"{query} hands-on practice exercises coding challenge"
        elif resource_type == "project":
            return f"{query} real-world project implementation build"
        elif resource_type == "article":
            return f"{query} guide best practices in-depth article"
        else:
            return query

    def _build_google_query(self, query: str, resource_type: str) -> str:
        """
        Build Google/SerpApi query — append type-specific terms.
        """
        if resource_type == "docs":
            return f"{query} official documentation OR reference guide"
        elif resource_type == "course":
            return f"{query} course OR tutorial OR learning path"
        elif resource_type == "practice":
            return f"{query} exercises OR challenges OR hands-on practice"
        elif resource_type == "project":
            return f"{query} project OR build OR implementation"
        elif resource_type == "article":
            return f"{query} guide OR article OR best practices"
        else:
            return query

    def _rank_and_deduplicate(
        self,
        items: List[Dict[str, Any]],
        current_level: Optional[str] = None,
        target_level: Optional[str] = None,
        available_hours_per_week: Optional[int] = None,
        week_number: Optional[int] = None,
        duration_weeks: Optional[int] = None,
        context_keywords: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        IMPROVED ranking with 10+ metrics for professional-grade resource selection.
        
        Scoring metrics:
        1. Trusted domain/channel (+14-20 points)
        2. Exact topic match (+15 points)
        3. Difficulty calibration (±10 points)
        4. Duration fitness (±8 points)
        5. Type appropriateness (+7-11 points)
        6. Search position (+10 points)
        7. Context keyword relevance (+24 points)
        8. Clickbait/noise detection (-7-15 points)
        9. Level progression awareness (±6 points)
        10. Stage-appropriate content (±6 points)
        11. Time utilization awareness (±4 points)
        12. Hands-on practical bonus (+4 points)
        13. Semantic duplicate penalty (-5 points)
        """
        seen = set()
        unique_items = []
        semantic_seen = {}  # For detecting semantic duplicates

        trusted_domains = [
            "developer.mozilla.org", "learn.microsoft.com", "angular.dev", "angular.io",
            "react.dev", "vuejs.org", "redux.js.org", "mobx.js.org", "jestjs.io",
            "testing-library.com", "web.dev", "developers.google.com", "freecodecamp.org",
            "frontendmasters.com", "codecademy.com", "coursera.org", "kaggle.com",
            "scikit-learn.org", "spark.apache.org", "hadoop.apache.org", "docs.python.org",
            "pandas.pydata.org", "numpy.org", "matplotlib.org",
            "w3schools.com", "geeksforgeeks.org", "realpython.com", "roadmap.sh",
            "frontendmentor.io", "github.com", "css-tricks.com", "smashingmagazine.com",
            "javascript.info", "typescript-lang.org", "typescriptlang.org", "dev.to",
        ]

        weak_domains = [
            "linkedin.com", "pinterest.com", "quora.com",
            "forum.freecodecamp.org", "skillshare.com",
        ]

        current_level = (current_level or "").lower()
        target_level = (target_level or "").lower()
        available_hours_per_week = available_hours_per_week or 20
        week_number = week_number or 1
        duration_weeks = duration_weeks or max(week_number, 1)
        project_stage_ratio = week_number / max(duration_weeks, 1)

        # Process keywords
        raw_keywords = context_keywords or []
        processed_keywords = []
        for kw in raw_keywords:
            parts = (
                str(kw).lower()
                .replace("—", " ")
                .replace(":", " ")
                .replace("(", " ")
                .replace(")", " ")
                .replace("/", " ")
                .split()
            )
            processed_keywords.extend([p for p in parts if len(p) > 2])

        processed_keywords = list(dict.fromkeys(processed_keywords))

        # Duration mapping for fitness checking
        duration_minutes = {
            "10 min": 10,
            "30 min": 30,
            "2 hours": 120,
            "full course": 480,  # Assume 8 hours minimum
        }

        def score(item: Dict[str, Any]) -> int:
            url = (item.get("url") or "").lower()
            title = (item.get("title") or "").lower()
            snippet = (item.get("snippet") or "").lower()
            source = (item.get("source") or "").lower()
            item_type = (item.get("type") or "").lower()
            channel = (item.get("channel") or "").lower()
            
            text = f"{title} {snippet} {source} {url}".lower()
            s = 0

            # ===== METRIC 1: Trusted Domain/Channel =====
            if any(domain in url for domain in trusted_domains):
                s += 20
            elif "tavily" in source:
                s += 5
            
            if item_type == "youtube" and item.get("is_trusted_channel"):
                s += 16  # Strong signal

            if any(domain in url for domain in weak_domains):
                s -= 8

            # ===== METRIC 2: Exact Topic Match =====
            if processed_keywords:
                # Exact phrase match is rare but very valuable
                for kw in processed_keywords:
                    if f'"{kw}"' in title or (kw + " " + processed_keywords[min(len(processed_keywords)-1, processed_keywords.index(kw)+1)]) in title:
                        s += 15

            # ===== METRIC 3: Type Appropriateness =====
            type_scores = {
                "docs": 11,
                "practice": 12,
                "project": 12,
                "course": 8,
                "youtube": 6,
                "article": 5,
            }
            s += type_scores.get(item_type, 2)

            # ===== METRIC 4: Search Position =====
            position = item.get("position", 999)
            s += max(0, 10 - min(position, 10))

            # ===== METRIC 5: Context Keyword Relevance =====
            if processed_keywords:
                matched = sum(1 for kw in processed_keywords if kw in text)
                if matched == 0:
                    s -= 25  # Strong penalty for zero relevance
                elif matched == 1:
                    s -= 5
                elif matched >= 3:
                    s += matched * 8  # Good reward for high relevance
                else:
                    s += matched * 5

            # ===== METRIC 6: Clickbait & Noise Detection =====
            if item_type == "youtube" and self._is_clickbait_youtube(title, snippet):
                s -= 15  # Strong penalty for clickbait

            noise = [
                "shorts", "vs ", "explained in", "in 5 minutes", "in 10 minutes",
                "for dummies", "top 100", "top 50", "top 10", "everything in",
                "ASMR", "shocking", "amazing", "incredible", "mind-blowing",
            ]
            if any(term in text for term in noise):
                s -= 7

            # ===== METRIC 7: Difficulty Calibration =====
            resource_difficulty = item.get("difficulty", "intermediate")
            level_values = {"beginner": 0, "beginner+": 1, "intermediate": 2, "advanced": 3}
            current_val = level_values.get(current_level, 1)
            target_val = level_values.get(target_level, 1)
            resource_val = level_values.get(resource_difficulty, 2)

            # Penalty if resource is too easy for target
            if resource_val < (current_val - 1):
                s -= 10
            # Penalty if resource is too hard
            elif resource_val > target_val + 1:
                s -= 5
            # Bonus if difficulty matches current→target
            elif current_val <= resource_val <= target_val:
                s += 8

            # ===== METRIC 8: Duration Fitness =====
            resource_duration_minutes = duration_minutes.get(item.get("duration", "2 hours"), 120)
            available_minutes = available_hours_per_week * 60 / 4  # 4 resources per week
            
            if resource_duration_minutes <= available_minutes:
                s += 8  # Good fit
            elif resource_duration_minutes <= available_minutes * 1.5:
                s += 4  # Slight oversize but acceptable
            else:
                s -= 5  # Too long for available time

            # ===== METRIC 9: Level Awareness =====
            if current_level == "none":
                if any(x in text for x in ["beginner", "getting started", "fundamentals"]):
                    s += 8
                if "advanced" in text:
                    s -= 5
            elif current_level == "beginner" and target_level == "intermediate":
                if "intermediate" in text:
                    s += 8
                if "beginner" in text:
                    s += 3
                if "advanced" in text:
                    s -= 3
            elif current_level == "intermediate":
                if any(x in text for x in ["advanced", "best practices", "optimization"]):
                    s += 7
                if "beginner" in text:
                    s -= 4

            # ===== METRIC 10: Stage Awareness =====
            if project_stage_ratio <= 0.33:
                if any(x in text for x in ["foundation", "fundamentals", "basics"]):
                    s += 6
                if any(x in text for x in ["capstone", "advanced project"]):
                    s -= 6
            elif project_stage_ratio <= 0.75:
                if any(x in text for x in ["project", "practice", "hands-on"]):
                    s += 6
            else:
                if any(x in text for x in ["capstone", "real world", "case study"]):
                    s += 7

            # ===== METRIC 11: Time Availability Awareness =====
            if available_hours_per_week <= 5:
                if "full course" in item.get("duration", ""):
                    s -= 4
                if item.get("duration") == "10 min" or item.get("duration") == "30 min":
                    s += 4
            elif available_hours_per_week >= 15:
                if "full course" in item.get("duration", ""):
                    s += 4

            # ===== METRIC 12: Practical Bonus =====
            if item_type in ("practice", "project") and any(
                x in text for x in ["exercise", "challenge", "hands-on", "project"]
            ):
                s += 5

            return s

        # First pass: collect and score
        scored_items = [(item, score(item)) for item in items]
        scored_items.sort(key=lambda x: x[1], reverse=True)

        # Second pass: deduplicate (exact URL + semantic)
        for item, item_score in scored_items:
            url = (item.get("url") or "").strip().lower()
            title_text = (item.get("title") or "").strip().lower()
            dedup_key = (url, title_text)
            
            if not url or dedup_key in seen:
                continue

            # ===== METRIC 13: Semantic Duplicate Penalty =====
            is_semantic_duplicate = False
            for existing_title, existing_score in semantic_seen.items():
                similarity = self._semantic_distance(title_text, existing_title)
                if similarity > 0.7:  # 70% similar = likely duplicate
                    is_semantic_duplicate = True
                    break

            if not is_semantic_duplicate:
                semantic_seen[title_text] = item_score
                seen.add(dedup_key)
                unique_items.append(item)

        return unique_items