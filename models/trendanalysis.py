import json
import time
import re
from datetime import datetime
from collections import defaultdict
import statistics


from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.prompts import PromptTemplate
from langchain_community.vectorstores import FAISS
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
from langchain_core.documents import Document
from dotenv import load_dotenv
import os

load_dotenv()

class UnifiedMarketIntelligence:
    def __init__(self, trends_file="trends_data.json"):
        self.trends_file = trends_file
        self.api_key = "AIzaSyCzpzTqKGTd5SVzmpCdZmaj2eFMiUR-nsI"
        self.driver = None
        self.wait = None
        self.is_logged_in = False
        
        # Initialize LLM for structured outputs
        self.llm = ChatGoogleGenerativeAI(
            model='gemini-2.0-flash',
            api_key=self.api_key,
            temperature=0.1  # Low for consistent structured output
        )
        
        self.setup_trend_system()
    
    def setup_trend_system(self):
        """Initialize trend analysis system"""
        try:
            self.docs = self.load_trends_data()
            if self.docs:
                self.embeddings = HuggingFaceEmbeddings(
                    model_name="sentence-transformers/all-MiniLM-L6-v2",
                    model_kwargs={'device': 'cpu'}
                )
                self.setup_retrieval()
                print("✅ Trend analysis system ready")
            else:
                print("⚠️ No trend data loaded - continuing with competitor analysis only")
        except Exception as e:
            print(f"⚠️ Trend system setup failed: {e}")
            self.docs = []
    
    def load_trends_data(self):
        """Load and process trends data"""
        try:
            with open(self.trends_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            documents = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                
                insights = self.extract_trend_insights(item)
                if insights:
                    doc = Document(
                        page_content="\n".join(insights),
                        metadata={
                            'platform': self.identify_platform(item),
                            'topic': item.get('topic', ''),
                            'date': item.get('data', {}).get('date', ''),
                            'source': item.get('data', {}).get('source', ''),
                        }
                    )
                    documents.append(doc)
            
            return documents
        except Exception as e:
            print(f"Error loading trends: {e}")
            return []
    
    def identify_platform(self, item):
        """Identify data platform"""
        content = str(item).lower()
        if 'instagram' in content or 'hashtag' in content:
            return 'instagram'
        elif 'google' in content or 'search_volume' in content:
            return 'google_trends'
        elif 'reddit' in content or 'upvotes' in content:
            return 'reddit'
        elif 'wikipedia' in content:
            return 'wikipedia'
        return 'other'
    
    def extract_trend_insights(self, item):
        """Extract insights from trend item"""
        insights = []
        topic = item.get('topic', 'Unknown')
        data = item.get('data', {})
        
        insights.append(f"Topic: {topic}")
        if 'source' in data:
            insights.append(f"Source: {data['source']}")
        if 'content' in data:
            content = data['content'][:150] + "..." if len(data['content']) > 150 else data['content']
            insights.append(f"Content: {content}")
        if 'metrics' in data and data['metrics']:
            insights.append(f"Metrics: {data['metrics']}")
        
        return insights
    
    def setup_retrieval(self):
        """Setup retrieval system"""
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800, chunk_overlap=100
        )
        split_docs = text_splitter.split_documents(self.docs)
        
        self.vector_store = FAISS.from_documents(split_docs, self.embeddings)
        self.semantic_retriever = self.vector_store.as_retriever(
            search_kwargs={'k': 8}
        )
        self.keyword_retriever = BM25Retriever.from_documents(split_docs)
        self.keyword_retriever.k = 6
        
        self.ensemble_retriever = EnsembleRetriever(
            retrievers=[self.semantic_retriever, self.keyword_retriever],
            weights=[0.6, 0.4]
        )
    
    def setup_driver(self):
        """Setup Chrome driver"""
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 15)
    
    def parse_number(self, text):
        """Parse Instagram numbers like 1.2K, 1.5M"""
        if not text:
            return 0
        
        clean_text = str(text).replace(',', '').replace(' ', '').strip()
        
        if 'M' in clean_text.upper():
            num = re.findall(r'[\d.]+', clean_text)[0] if re.findall(r'[\d.]+', clean_text) else '0'
            try:
                return int(float(num) * 1000000)
            except:
                pass
        elif 'K' in clean_text.upper():
            num = re.findall(r'[\d.]+', clean_text)[0] if re.findall(r'[\d.]+', clean_text) else '0'
            try:
                return int(float(num) * 1000)
            except:
                pass
        elif 'B' in clean_text.upper():
            num = re.findall(r'[\d.]+', clean_text)[0] if re.findall(r'[\d.]+', clean_text) else '0'
            try:
                return int(float(num) * 1000000000)
            except:
                pass
        
        numbers = re.findall(r'\d+', clean_text)
        if numbers:
            try:
                return int(''.join(numbers))
            except:
                pass
        
        return 0
    
    def login_instagram(self):
        """Login to Instagram"""
        if self.is_logged_in:
            return True
        
        try:
            print("🔐 Logging into Instagram...")
            self.driver.get("https://www.instagram.com/accounts/login/")
            time.sleep(3)
            
            # Accept cookies
            try:
                accept_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Allow')]")
                accept_btn.click()
                time.sleep(1)
            except:
                pass
            
            # Login
            username_input = self.wait.until(EC.element_to_be_clickable((By.NAME, "username")))
            password_input = self.driver.find_element(By.NAME, "password")
            
            username_input.clear()
            username_input.send_keys(os.getenv("IG_USERNAME"))
            time.sleep(1)
            
            password_input.clear()
            password_input.send_keys(os.getenv("IG_PASSWORD"))
            password_input.send_keys(Keys.RETURN)
            time.sleep(5)
            
            if "login" not in self.driver.current_url.lower():
                self.is_logged_in = True
                print("✅ Login successful")
                
                # Dismiss popups
                try:
                    not_now = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Not Now')]")
                    not_now.click()
                except:
                    pass
                
                return True
            
            return False
            
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False
    
    def get_followers(self):
        """Get follower count"""
        selectors = [
            "//a[contains(@href, '/followers/')]/span",
            "//a[contains(@href, '/followers/')]/span[@title]",
            "//span[@title and ancestor::a[contains(@href, '/followers/')]]"
        ]
        
        for selector in selectors:
            try:
                element = self.driver.find_element(By.XPATH, selector)
                followers_text = element.get_attribute("title") or element.text
                if followers_text:
                    return self.parse_number(followers_text)
            except:
                continue
        return 0
    
    def extract_post_likes(self):
        """Extract likes from post modal"""
        selectors = [
            "//span[contains(text(), 'likes')]",
            "//a[contains(@href, 'liked_by')]//span",
            "//button[contains(@class, 'like')]//span"
        ]
        
        for selector in selectors:
            try:
                element = self.driver.find_element(By.XPATH, selector)
                likes_text = element.text
                if 'like' in likes_text.lower():
                    numbers = re.findall(r'[\d,KMB.]+', likes_text)
                    if numbers:
                        return self.parse_number(numbers[0])
            except:
                continue
        return 0
    
    def extract_post_comments(self):
        """Extract comment count"""
        try:
            comment_elements = self.driver.find_elements(By.XPATH, "//div[contains(@class, 'comment')]")
            return len(comment_elements)
        except:
            return 0
    
    def extract_caption(self):
        """Extract post caption"""
        selectors = [
            "//article//div[contains(@class, 'caption')]//span",
            "//span[contains(text(), '#')]/..",
            "//div[contains(@role, 'button')]//span[string-length(text()) > 20]"
        ]
        
        for selector in selectors:
            try:
                element = self.driver.find_element(By.XPATH, selector)
                caption = element.text.strip()
                if len(caption) > 10:
                    return caption[:200]
            except:
                continue
        return ""
    
    def close_post_modal(self):
        """Close post modal"""
        try:
            close_selectors = [
                "//button[contains(@aria-label, 'Close')]",
                "//svg[@aria-label='Close']/.."
            ]
            
            for selector in close_selectors:
                try:
                    close_btn = self.driver.find_element(By.XPATH, selector)
                    close_btn.click()
                    return
                except:
                    continue
            
            # Fallback: Escape key
            self.driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        except:
            pass
    
    def analyze_competitor_posts(self, max_posts=5):
        """Analyze competitor posts"""
        try:
            post_elements = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")[:max_posts]
            engagement_data = []
            
            for i, post_element in enumerate(post_elements):
                try:
                    print(f"  📸 Analyzing post {i+1}/{len(post_elements)}")
                    
                    # Click post
                    self.driver.execute_script("arguments[0].click();", post_element)
                    time.sleep(3)
                    
                    # Extract data
                    likes = self.extract_post_likes()
                    comments = self.extract_post_comments()
                    caption = self.extract_caption()
                    
                    engagement_data.append({
                        'likes': likes,
                        'comments': comments,
                        'caption': caption
                    })
                    
                    self.close_post_modal()
                    time.sleep(2)
                    
                except Exception as e:
                    print(f"  ⚠️ Error with post {i+1}: {e}")
                    self.close_post_modal()
                    continue
            
            return engagement_data
            
        except Exception as e:
            print(f"Error analyzing posts: {e}")
            return []
    
    def scrape_competitor(self, profile_url):
        """Scrape competitor profile"""
        username = profile_url.rstrip('/').split('/')[-1]
        print(f"🔍 Analyzing @{username}...")
        
        try:
            self.driver.get(profile_url)
            time.sleep(4)
            
            # Get metrics
            followers = self.get_followers()
            engagement_data = self.analyze_competitor_posts()
            
            if engagement_data:
                avg_likes = sum(p['likes'] for p in engagement_data) // len(engagement_data)
                avg_comments = sum(p['comments'] for p in engagement_data) // len(engagement_data)
                engagement_rate = ((avg_likes + avg_comments) / followers * 100) if followers > 0 else 0
                captions = [p['caption'] for p in engagement_data if p['caption']]
            else:
                avg_likes = avg_comments = engagement_rate = 0
                captions = []
            
            result = {
                "username": username,
                "followers": followers,
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "engagement_rate": round(engagement_rate, 2),
                "posts_analyzed": len(engagement_data),
                "sample_captions": captions[:3],
                "total_engagement": avg_likes + avg_comments
            }
            
            print(f"  ✅ @{username}: {followers:,} followers, {avg_likes:,} avg likes, {engagement_rate:.1f}% ER")
            return result
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return {"error": str(e), "username": username}
    
    def get_trend_context(self, niche, query_terms):
        """Get relevant trend context"""
        if not hasattr(self, 'ensemble_retriever'):
            return "No trend data available"
        
        try:
            search_query = f"{niche} {' '.join(query_terms)} trends hashtags content"
            relevant_docs = self.ensemble_retriever.get_relevant_documents(search_query)
            
            if not relevant_docs:
                return "No specific trend data found for this niche"
            
            context = []
            for doc in relevant_docs[:5]:  # Top 5 most relevant
                context.append(doc.page_content)
            
            return "\n---\n".join(context)
            
        except Exception as e:
            return f"Trend analysis unavailable: {e}"
    
    def generate_unified_analysis(self, brand_niche, competitor_data, trend_context):
        """Generate unified structured analysis"""
        
        # Calculate competitor benchmarks
        valid_competitors = [c for c in competitor_data if 'error' not in c]
        
        if not valid_competitors:
            return "❌ No valid competitor data to analyze"
        
        # Competitor stats
        total_followers = sum(c['followers'] for c in valid_competitors)
        avg_followers = total_followers // len(valid_competitors)
        avg_engagement_rate = sum(c['engagement_rate'] for c in valid_competitors) / len(valid_competitors)
        top_performer = max(valid_competitors, key=lambda x: x['total_engagement'])
        
        # All captions for content analysis
        all_captions = []
        for comp in valid_competitors:
            all_captions.extend(comp.get('sample_captions', []))
        
        prompt = PromptTemplate.from_template(
"""
You are a content strategist analyzing market data to generate viral content ideas.

BRAND NICHE: {niche}
COMPETITOR DATA: {competitor_summary}
TRENDING TOPICS: {trends}

Generate EXACTLY 3 content ideas in this format:

## 💡 TOP 3 CONTENT IDEAS

### 1. [Content Type/Format]
**Why it works:** [Data-backed reason from trends/competitors]
**Success potential:** [High/Medium rating with %]
**Recommended songs:** 
- [Trending song 1] - [Why it fits]
- [Trending song 2] - [Why it fits]

### 2. [Content Type/Format]  
**Why it works:** [Data-backed reason from trends/competitors]
**Success potential:** [High/Medium rating with %]
**Recommended songs:**
- [Trending song 1] - [Why it fits]
- [Trending song 2] - [Why it fits]

### 3. [Content Type/Format]
**Why it works:** [Data-backed reason from trends/competitors]  
**Success potential:** [High/Medium rating with %]
**Recommended songs:**
- [Trending song 1] - [Why it fits]
- [Trending song 2] - [Why it fits]

REQUIREMENTS:
- Base ideas ONLY on provided competitor/trend data
- Include viral potential percentage (60-95%)
- Suggest current trending songs relevant to each idea
- Keep each idea under 50 words
- Focus on reels and posts only
""")
        
        
        comp_summary = ""
        for comp in valid_competitors:
            comp_summary += f"@{comp['username']}: {comp['followers']:,} followers, {comp['avg_likes']:,} avg likes, {comp['engagement_rate']}% ER\n"
            if comp['sample_captions']:
                comp_summary += f"Content themes: {comp['sample_captions'][0][:100]}...\n"
        
        formatted_prompt = prompt.format(
            niche=brand_niche,
            competitor_summary=comp_summary,
            trends=trend_context,
            avg_followers=avg_followers,
            target_er=round(avg_engagement_rate * 1.2, 1),  # 20% above average
            top_performer=top_performer['username'],
            top_engagement=top_performer['total_engagement']
        )
        
        try:
            response = self.llm.invoke(formatted_prompt)
            return response.content
        except Exception as e:
            return f"Analysis generation error: {e}"
    
    def run_unified_analysis(self):
        """Main unified analysis flow"""
        print("🎯 SMB Market Intelligence System")
        print("=" * 50)
        
        # Get inputs
        brand_niche = input("Enter your brand niche: ").strip()
        if not brand_niche:
            print("❌ Brand niche required")
            return
        
        competitors = []
        print(f"\nEnter competitor Instagram profiles (2-3 recommended):")
        for i in range(3):
            url = input(f"Competitor {i+1} URL (or press Enter to skip): ").strip()
            if url and "instagram.com" in url:
                competitors.append(url)
            elif url:
                print("⚠️ Please provide valid Instagram URLs")
        
        if not competitors:
            print("❌ At least one competitor required")
            return
        
        print(f"\n📊 Analyzing {len(competitors)} competitors + market trends...")
        print("-" * 50)
        
        # Setup browser
        self.setup_driver()
        
        if not self.login_instagram():
            print("❌ Instagram login failed - check credentials")
            return
        
        # Scrape competitors
        competitor_data = []
        for url in competitors:
            result = self.scrape_competitor(url)
            competitor_data.append(result)
            time.sleep(3)  # Rate limiting
        
        # Get trend context
        query_terms = [brand_niche] + [comp.get('username', '') for comp in competitor_data if comp.get('username')]
        trend_context = self.get_trend_context(brand_niche, query_terms)
        
        print("\n🤖 Generating unified market intelligence...")
        
        # Generate analysis
        analysis = self.generate_unified_analysis(brand_niche, competitor_data, trend_context)
        
        # Display results
        print("\n" + "=" * 60)
        print("🔥 MARKET INTELLIGENCE REPORT")
        print("=" * 60)
        print(analysis)
        
        # Save report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"market_intelligence_{brand_niche.replace(' ', '_')}_{timestamp}.txt"
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"SMB MARKET INTELLIGENCE REPORT\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Brand Niche: {brand_niche}\n")
            f.write(f"Competitors Analyzed: {len([c for c in competitor_data if 'error' not in c])}\n\n")
            
            f.write("RAW COMPETITOR DATA:\n")
            f.write("-" * 30 + "\n")
            for comp in competitor_data:
                f.write(f"{json.dumps(comp, indent=2)}\n\n")
            
            f.write("MARKET INTELLIGENCE ANALYSIS:\n")
            f.write("-" * 30 + "\n")
            f.write(analysis)
        
        print(f"\n💾 Full report saved: {filename}")
        
        # Cleanup
        if self.driver:
            self.driver.quit()

if __name__ == "__main__":
    system = UnifiedMarketIntelligence()
    system.run_unified_analysis()