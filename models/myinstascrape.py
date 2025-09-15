from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from dotenv import load_dotenv
import os
import time
import re
import json
from datetime import datetime
import random

load_dotenv()

class InstagramCompetitorAnalyzer:
    def __init__(self):
        self.driver = self._setup_driver()
        self.wait = WebDriverWait(self.driver, 20)
        self.is_logged_in = False
        
    def _setup_driver(self):
        """Setup Chrome driver with optimal settings"""
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")  # Remove this line to see browser
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        driver = webdriver.Chrome(options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    
    def _random_delay(self, min_seconds=2, max_seconds=5):
        """Add random delay to avoid detection"""
        time.sleep(random.uniform(min_seconds, max_seconds))
    
    def _parse_number(self, text):
        """Parse Instagram number formats like 1.2K, 1.5M, 1,234"""
        if not text:
            return 0
        
        clean_text = str(text).replace(',', '').replace(' ', '').strip().upper()
        
        multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000}
        
        for suffix, multiplier in multipliers.items():
            if suffix in clean_text:
                number_part = re.findall(r'[\d.]+', clean_text)
                if number_part:
                    try:
                        return int(float(number_part[0]) * multiplier)
                    except:
                        pass
        
        # For plain numbers
        numbers_only = re.findall(r'\d+', clean_text)
        if numbers_only:
            try:
                return int(''.join(numbers_only))
            except:
                pass
        
        return 0
    
    def _login(self):
        """Enhanced login with better error handling"""
        if self.is_logged_in:
            return True
            
        try:
            print("🔐 Logging into Instagram...")
            self.driver.get("https://www.instagram.com/accounts/login/")
            self._random_delay(3, 5)
            
            # Handle cookie popup
            try:
                cookie_buttons = [
                    "//button[contains(text(), 'Accept')]",
                    "//button[contains(text(), 'Allow')]",
                    "//button[contains(text(), 'Only allow essential')]"
                ]
                for button_xpath in cookie_buttons:
                    try:
                        button = self.driver.find_element(By.XPATH, button_xpath)
                        button.click()
                        self._random_delay(1, 2)
                        break
                    except:
                        continue
            except:
                pass
            
            # Login credentials
            username_input = self.wait.until(EC.element_to_be_clickable((By.NAME, "username")))
            password_input = self.driver.find_element(By.NAME, "password")
            
            # Simulate human typing
            username = os.getenv("IG_USERNAME")
            password = os.getenv("IG_PASSWORD")
            
            if not username or not password:
                print("❌ Instagram credentials not found in .env file")
                return False
            
            for char in username:
                username_input.send_keys(char)
                time.sleep(random.uniform(0.1, 0.3))
            
            self._random_delay(1, 2)
            
            for char in password:
                password_input.send_keys(char)
                time.sleep(random.uniform(0.1, 0.3))
            
            password_input.send_keys(Keys.RETURN)
            self._random_delay(5, 7)
            
            # Check login success
            current_url = self.driver.current_url
            if "instagram.com" in current_url and "login" not in current_url:
                self.is_logged_in = True
                print("✅ Login successful")
                
                # Handle post-login popups
                popup_buttons = [
                    "//button[contains(text(), 'Not Now')]",
                    "//button[contains(text(), 'Not now')]",
                    "//button[contains(text(), 'Skip')]",
                    "//button[@type='button'][contains(., 'Not Now')]"
                ]
                
                for button_xpath in popup_buttons:
                    try:
                        button = self.wait.until(EC.element_to_be_clickable((By.XPATH, button_xpath)))
                        button.click()
                        self._random_delay(1, 2)
                    except:
                        pass
                
                return True
            else:
                print("❌ Login failed - check credentials")
                return False
                
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False
    
    def _get_profile_stats(self):
        """Get follower count and following from profile header"""
        try:
            # Wait for profile header to load
            self._random_delay(2, 4)
            
            # Multiple selectors for followers
            followers_selectors = [
                "//a[contains(@href, '/followers/')]/span",
                "//a[contains(@href, '/followers/')]/span[@title]",
                "//header//a[contains(@href, 'followers')]//span",
                "//div[contains(text(), 'followers')]/..//span"
            ]
            
            followers = 0
            for selector in followers_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        text = element.get_attribute("title") or element.text
                        if text and any(char.isdigit() for char in text):
                            parsed = self._parse_number(text)
                            if parsed > followers:
                                followers = parsed
                            break
                    if followers > 0:
                        break
                except:
                    continue
            
            return followers
            
        except Exception as e:
            print(f"⚠️ Error getting profile stats: {e}")
            return 0
    
    def _get_post_links(self, max_posts=10):
        """Get links to recent posts"""
        try:
            # Scroll to load posts
            for _ in range(3):
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                self._random_delay(2, 3)
            
            # Get post links
            post_elements = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")
            post_links = []
            
            for element in post_elements[:max_posts]:
                href = element.get_attribute('href')
                if href and '/p/' in href:
                    post_links.append(href)
            
            return list(set(post_links))[:max_posts]  # Remove duplicates
            
        except Exception as e:
            print(f"⚠️ Error getting post links: {e}")
            return []
    
    def _analyze_post(self, post_url):
        """Analyze individual post for likes, comments, and caption"""
        try:
            self.driver.get(post_url)
            self._random_delay(3, 5)
            
            # Extract likes
            likes = self._extract_post_likes()
            
            # Extract comments count
            comments = self._extract_post_comments()
            
            # Extract full caption
            caption = self._extract_post_caption()
            
            return {
                'url': post_url,
                'likes': likes,
                'comments': comments,
                'caption': caption
            }
            
        except Exception as e:
            print(f"  ⚠️ Error analyzing post: {e}")
            return {
                'url': post_url,
                'likes': 0,
                'comments': 0,
                'caption': ""
            }
    
    def _extract_post_likes(self):
        """Extract likes from post page"""
        selectors = [
            "//button[contains(@class, '_abl-')]//span",  # New Instagram UI
            "//section//button//span[contains(text(), 'like')]",
            "//span[contains(text(), 'like')]",
            "//button[contains(@aria-label, 'like')]//span",
            "//a[contains(@href, 'liked_by')]",
            "//span[contains(@title, 'like')]"
        ]
        
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.get_attribute('title') or element.text
                    if text and ('like' in text.lower() or any(char.isdigit() for char in text)):
                        parsed = self._parse_number(text)
                        if parsed > 0:
                            return parsed
            except:
                continue
        
        return 0
    
    def _extract_post_comments(self):
        """Extract comments count from post page"""
        try:
            # Look for "View all X comments" text
            view_comments_selectors = [
                "//button[contains(text(), 'View all')]",
                "//span[contains(text(), 'comment')]",
                "//button[contains(text(), 'comment')]"
            ]
            
            for selector in view_comments_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        text = element.text
                        if 'comment' in text.lower():
                            numbers = re.findall(r'\d+', text)
                            if numbers:
                                return int(numbers[0])
                except:
                    continue
            
            # Fallback: count visible comment elements
            comment_elements = self.driver.find_elements(By.XPATH, "//div[@role='button']//span[contains(@dir, 'auto')]")
            return len([el for el in comment_elements if el.text and len(el.text) > 10])
            
        except:
            return 0
    
    def _extract_post_caption(self):
        """Extract full caption from post page"""
        selectors = [
            "//article//h1",  # Post caption in h1
            "//span[contains(@class, '_aacl _aaco _aacu _aacx _aad7 _aade')]",  # Instagram caption class
            "//div[@data-testid='post-caption']",
            "//article//div//span[string-length(text()) > 20]",
            "//span[@dir='auto'][string-length(text()) > 15]"
        ]
        
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip()
                    if len(text) > 15 and not text.startswith('@') and '#' in text or len(text) > 30:
                        return text[:500]  # Limit caption length
            except:
                continue
        
        return ""
    
    def scrape_competitor(self, profile_url):
        """Scrape complete competitor data"""
        if not self._login():
            return {"error": "Login failed"}
        
        username = profile_url.rstrip('/').split('/')[-1]
        print(f"\n🔍 Analyzing @{username}...")
        
        try:
            # Go to profile
            self.driver.get(profile_url)
            self._random_delay(3, 5)
            
            # Get follower count
            followers = self._get_profile_stats()
            print(f"  👥 Followers: {followers:,}")
            
            # Get recent post links
            post_links = self._get_post_links(10)
            print(f"  📸 Found {len(post_links)} recent posts")
            
            # Analyze each post
            posts_data = []
            for i, post_url in enumerate(post_links, 1):
                print(f"  📊 Analyzing post {i}/10...")
                post_data = self._analyze_post(post_url)
                posts_data.append(post_data)
                self._random_delay(2, 4)  # Rate limiting
            
            # Calculate averages
            if posts_data:
                total_likes = sum(p['likes'] for p in posts_data)
                total_comments = sum(p['comments'] for p in posts_data)
                avg_likes = total_likes // len(posts_data)
                avg_comments = total_comments // len(posts_data)
                engagement_rate = ((avg_likes + avg_comments) / followers * 100) if followers > 0 else 0
            else:
                avg_likes = avg_comments = engagement_rate = 0
            
            # Collect all captions for analysis
            captions = [p['caption'] for p in posts_data if p['caption']]
            
            result = {
                "username": username,
                "profile_url": profile_url,
                "followers": followers,
                "posts_analyzed": len(posts_data),
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "engagement_rate": round(engagement_rate, 2),
                "total_posts_engagement": total_likes + total_comments,
                "captions": captions,
                "posts_data": posts_data
            }
            
            print(f"  ✅ @{username}: {followers:,} followers, {avg_likes:,} avg likes, {engagement_rate:.1f}% engagement")
            return result
            
        except Exception as e:
            print(f"  ❌ Error analyzing @{username}: {e}")
            return {"error": str(e), "username": username}
    
    def generate_analysis(self, brand_description, competitors_data):
        """Generate tailored competitive analysis"""
        try:
            # Filter valid competitor data
            valid_competitors = [c for c in competitors_data if 'error' not in c and c.get('followers', 0) > 0]
            
            if not valid_competitors:
                return "❌ No valid competitor data found for analysis"
            
            # Initialize Gemini
            llm = ChatGoogleGenerativeAI(
                model='gemini-1.5-flash',
                temperature=0.2,
                api_key=os.getenv('GOOGLE_API_KEY')  # Make sure this is in your .env
            )
            
            # Prepare competitor summary
            competitor_summary = []
            all_captions = []
            
            for comp in valid_competitors:
                competitor_summary.append(f"""
@{comp['username']}:
• Followers: {comp['followers']:,}
• Average Likes: {comp['avg_likes']:,}
• Average Comments: {comp['avg_comments']:,}
• Engagement Rate: {comp['engagement_rate']:.2f}%
• Posts Analyzed: {comp['posts_analyzed']}
• Total Engagement: {comp.get('total_posts_engagement', 0):,}
""")
                all_captions.extend(comp.get('captions', [])[:3])  # Top 3 captions per competitor
            
            prompt = PromptTemplate.from_template("""
Analyze Instagram competitors for: {brand}

COMPETITOR DATA:
{competitors}

COMPETITOR CAPTIONS:
{captions}

Extract and analyze ONLY the following 3 areas:

## 1. FOLLOWER ANALYSIS
- List each competitor's follower count
- Average follower range across competitors
- Follower growth benchmark for {brand}

## 2. HASHTAG STRATEGY
- Group hashtags by category (branded, industry, trending, niche)
- List top 15 most frequently used hashtags


## 3. CONTENT & ENGAGEMENT
- Identify top 5 post themes (e.g. product showcase, user-generated content, educational, behind-the-scenes)
- Average engagement rates per content type
Focus on extracting concrete data: specific follower numbers, exact hashtags, actual caption examples, measurable patterns. No fluff - just actionable data analysis.
""")
            
            # Generate analysis
            response = llm.invoke(prompt.format(
                brand=brand_description,
                competitors='\n'.join(competitor_summary),
                captions='\n\n'.join([f"Caption {i+1}: {cap[:200]}..." for i, cap in enumerate(all_captions[:6])])
            ))
            
            return response.content
            
        except Exception as e:
            return f"❌ Analysis generation error: {e}"
    
    def run(self):
        """Main execution function"""
        print("🎯 INSTAGRAM COMPETITOR ANALYZER")
        print("=" * 50)
        
        # Get brand information
        brand = input("📝 Describe your brand/niche: ").strip()
        if not brand:
            print("❌ Brand description is required")
            return
        
        # Get competitor profiles
        print(f"\n📋 Enter competitor Instagram URLs:")
        competitors = []
        
        for i in range(5):  # Allow up to 5 competitors
            url = input(f"Competitor {i+1} URL (or press Enter to finish): ").strip()
            if not url:
                break
            elif "instagram.com/" in url and not url.endswith('/'):
                competitors.append(url)
            elif "instagram.com/" in url:
                competitors.append(url)
            else:
                print("⚠️ Please provide valid Instagram profile URLs")
        
        if not competitors:
            print("❌ At least one competitor URL is required")
            return
        
        print(f"\n🚀 Starting analysis of {len(competitors)} competitors...")
        print("-" * 50)
        
        # Analyze competitors
        results = []
        for i, url in enumerate(competitors, 1):
            print(f"\n📊 Competitor {i}/{len(competitors)}")
            result = self.scrape_competitor(url)
            results.append(result)
            
            if i < len(competitors):  # Don't wait after last competitor
                print("⏳ Waiting before next analysis...")
                self._random_delay(8, 12)
        
        # Generate insights
        print(f"\n🤖 Generating competitive analysis...")
        analysis = self.generate_analysis(brand, results)
        
        # Display results
        print("\n" + "=" * 60)
        print("🔥 COMPETITIVE ANALYSIS RESULTS")
        print("=" * 60)
        print(analysis)
        
        # Save comprehensive report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"instagram_competitor_analysis_{timestamp}.json"
        
        report_data = {
            "analysis_date": datetime.now().isoformat(),
            "brand": brand,
            "competitors_analyzed": len(competitors),
            "raw_data": results,
            "analysis": analysis,
            "summary_stats": {
                "avg_followers": sum(r.get('followers', 0) for r in results if 'error' not in r) // max(len([r for r in results if 'error' not in r]), 1),
                "avg_engagement_rate": sum(r.get('engagement_rate', 0) for r in results if 'error' not in r) / max(len([r for r in results if 'error' not in r]), 1),
                "total_posts_analyzed": sum(r.get('posts_analyzed', 0) for r in results if 'error' not in r)
            }
        }
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Complete report saved: {filename}")
        
        # Cleanup
        self.driver.quit()
        print("\n✅ Analysis complete!")

