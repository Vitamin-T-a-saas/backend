from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from dotenv import load_dotenv
from apify_client import ApifyClient
import os
import json
import time
from datetime import datetime
from urllib.parse import urlparse

load_dotenv()


class InstagramCompetitorAnalyzer:
    def __init__(self):
        self.apify = ApifyClient(os.getenv("APIFY_TOKEN"))

    def extract_username(self, profile_url: str) -> str:
        """Extract username from Instagram URL"""
        path = urlparse(profile_url).path
        username = path.strip("/").split("/")[0]
        return username

    def fetch_profile(self, username: str) -> dict:
        """Fetch Instagram profile data with posts"""
        try:
            run = self.apify.actor("apify/instagram-profile-scraper").call(
                run_input={
                    "usernames": [username],
                    "resultsLimit": 1,
                    "resultsType": "posts",  # Include posts in response
                    "proxyConfiguration": {"useApifyProxy": True},
                }
            )

            dataset_id = run["defaultDatasetId"]
            print(f"[DEBUG] Profile dataset_id: {dataset_id}")
            items = self.apify.dataset(dataset_id).list_items().items
            
            if not items:
                return None
            
            print(f"[DEBUG] Profile data retrieved for @{username}")
            return items[0]
        except Exception as e:
            print(f"[ERROR] Profile fetch failed: {e}")
            return None

    def extract_posts_from_profile(self, profile: dict, limit: int = 10) -> list:
        """Extract posts data from profile response"""
        try:
            # Posts are included in the profile scraper response
            posts = profile.get("latestPosts", [])
            
            if not posts:
                print("[WARNING] No posts found in profile data")
                return []
            
            posts_data = []
            for post in posts[:limit]:
                posts_data.append({
                    "url": post.get("url", ""),
                    "likes": post.get("likesCount", 0),
                    "comments": post.get("commentsCount", 0),
                    "caption": (post.get("caption") or "")[:500],
                    "type": post.get("type", ""),
                })

            print(f"[DEBUG] Extracted {len(posts_data)} posts from profile")
            return posts_data
        except Exception as e:
            print(f"[ERROR] Post extraction failed: {e}")
            return []

    def scrape_competitor(self, profile_url: str) -> dict:
        """Main scraping function for a single competitor"""
        username = self.extract_username(profile_url)
        print(f"\n🔍 Analyzing @{username}...")

        try:
            # Fetch profile data (includes posts)
            profile = self.fetch_profile(username)
            
            if not profile:
                return {"error": "Profile not found", "username": username}

            followers = profile.get("followersCount", 0)
            
            if followers == 0:
                return {"error": "No followers data", "username": username}

            # Extract posts from profile response
            posts_data = self.extract_posts_from_profile(profile, limit=10)
            
            if not posts_data:
                return {"error": "No posts found", "username": username}

            # Calculate metrics
            avg_likes = sum(p["likes"] for p in posts_data) // max(len(posts_data), 1)
            avg_comments = sum(p["comments"] for p in posts_data) // max(len(posts_data), 1)
            
            engagement_rate = (
                ((avg_likes + avg_comments) / followers) * 100
                if followers >= 100
                else 0
            )

            return {
                "username": username,
                "profile_url": profile_url,
                "followers": followers,
                "posts_analyzed": len(posts_data),
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "engagement_rate": round(engagement_rate, 2),
                "captions": [p["caption"] for p in posts_data if p["caption"]],
                "posts_data": posts_data,
            }

        except Exception as e:
            print(f"[ERROR] Scraping failed: {e}")
            return {"error": str(e), "username": username}

    def clean_json_response(self, response_text: str) -> str:
        """Clean LLM response to extract valid JSON"""
        # Remove markdown code blocks
        response_text = response_text.replace("```json", "").replace("```", "")
        
        # Remove any text before the first [ or {
        start_idx = min(
            response_text.find("[") if response_text.find("[") != -1 else len(response_text),
            response_text.find("{") if response_text.find("{") != -1 else len(response_text)
        )
        
        if start_idx < len(response_text):
            response_text = response_text[start_idx:]
        
        return response_text.strip()

    def generate_analysis(self, brand_description: str, competitors_data: list) -> dict:
        """Generate competitive analysis using LLM"""
        print("[DEBUG] Starting LLM analysis...")
        
        # Filter valid competitors
        valid_competitors = [
            c for c in competitors_data 
            if "error" not in c and c.get("followers", 0) > 0
        ]

        if not valid_competitors:
            print("[ERROR] No valid competitor data")
            return {
                "error": "No valid competitor data", 
                "raw_data": competitors_data
            }

        print(f"[DEBUG] Analyzing {len(valid_competitors)} valid competitors")

        try:
            llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                temperature=0.2,
                api_key=os.getenv("GOOGLE_API_KEY"),
            )

            prompt = PromptTemplate.from_template("""
You are a social media marketing analyst. Analyze the following Instagram competitors for the brand: {brand}

Competitor data:
{competitors}

Return a JSON array with one analysis object per competitor. Use this exact format:

[
  {{
    "username": "competitor_username",
    "followers": number,
    "avg_likes": number,
    "avg_comments": number,
    "engagement_rate": "X%",
    "trendy_captions": ["caption 1", "caption 2", "caption 3"],
    "content_classification": {{
      "product_promo": "X%",
      "lifestyle_story": "X%",
      "educational": "X%",
      "behind_the_scenes": "X%",
      "trends": "X%",
      "brand_collabs": "X%",
      "community_building": "X%",
      "ugc": "X%"
    }},
    "top_brand_deals": ["Brand 1", "Brand 2", "Brand 3"],
    "growth_strategy": "2-3 sentence summary of their growth tactics",
    "opportunity_gap": "2-3 sentence summary of opportunities for the brand"
  }}
]

Important: Return ONLY valid JSON, no additional text or explanations.
""")

            response = llm.invoke(
                prompt.format(
                    brand=brand_description,
                    competitors=json.dumps(valid_competitors, indent=2),
                )
            )

            print("[DEBUG] LLM response received")
            
            # Clean and parse response
            cleaned_response = self.clean_json_response(response.content)
            
            try:
                analysis = json.loads(cleaned_response)
                print("[DEBUG] Successfully parsed JSON response")
                return analysis
            except json.JSONDecodeError as e:
                print(f"[ERROR] JSON parsing failed: {e}")
                print(f"[DEBUG] Raw response: {response.content[:500]}...")
                return {
                    "error": "Invalid JSON from LLM",
                    "raw_output": response.content,
                }

        except Exception as e:
            print(f"[ERROR] LLM analysis failed: {e}")
            return {
                "error": f"LLM error: {str(e)}",
                "valid_competitors": valid_competitors
            }

    def run(self):
        """Main execution flow"""
        print("🎯 INSTAGRAM COMPETITOR ANALYZER")
        print("=" * 50)

        # Get brand description
        brand = input("📝 Describe your brand/niche: ").strip()
        if not brand:
            print("❌ Brand description required")
            return

        # Get competitor URLs
        competitors = []
        print("\n📋 Enter competitor Instagram URLs:")

        for i in range(5):
            url = input(f"Competitor {i+1} URL (Enter to stop): ").strip()
            if not url:
                break
            if "instagram.com/" in url:
                competitors.append(url)
            else:
                print("⚠️  Invalid Instagram URL, skipping...")

        if not competitors:
            print("❌ No competitors provided")
            return

        print(f"\n🚀 Starting analysis of {len(competitors)} competitors...")
        print("-" * 50)

        # Scrape competitors
        results = []
        for i, url in enumerate(competitors, 1):
            print(f"\n📊 Competitor {i}/{len(competitors)}")
            result = self.scrape_competitor(url)
            results.append(result)
            
            # Add delay between requests (except after last one)
            if i < len(competitors):
                print("⏳ Waiting 5 seconds...")
                time.sleep(5)

        # Generate analysis
        print("\n🤖 Generating competitive analysis...")
        analysis = self.generate_analysis(brand, results)

        # Display results
        print("\n" + "=" * 60)
        print("🔥 COMPETITIVE ANALYSIS RESULTS")
        print("=" * 60)
        print(json.dumps(analysis, indent=2))

        # Calculate summary stats
        valid_results = [r for r in results if "error" not in r]
        summary_stats = {
            "total_competitors_analyzed": len(competitors),
            "successful_analyses": len(valid_results),
            "avg_followers": (
                sum(r.get("followers", 0) for r in valid_results) // max(len(valid_results), 1)
            ),
            "avg_engagement_rate": (
                round(
                    sum(r.get("engagement_rate", 0) for r in valid_results) / max(len(valid_results), 1),
                    2
                )
            ),
            "total_posts_analyzed": sum(r.get("posts_analyzed", 0) for r in valid_results),
        }

        # Save comprehensive report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"instagram_competitor_analysis_{timestamp}.json"

        report_data = {
            "analysis_date": datetime.now().isoformat(),
            "brand": brand,
            "summary_stats": summary_stats,
            "raw_data": results,
            "analysis": analysis,
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Report saved: {filename}")
        print("✅ Done!")


if __name__ == "__main__":
    analyzer = InstagramCompetitorAnalyzer()
    analyzer.run()