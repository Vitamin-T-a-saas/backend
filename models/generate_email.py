from typing import TypedDict, List, Annotated, Literal
import json
import os
from datetime import datetime

def run_email_agent():
    """Main function to run the email agent - call this from main.py"""
    
    try:
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph.message import add_messages
        from langchain.tools import tool
        from langgraph.prebuilt import ToolNode
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        
        # Import Pydantic AFTER LangChain to avoid conflicts
        from pydantic import BaseModel, Field
        
        def get_llm_local():
            """Get LLM instance locally to avoid circular import"""
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                temperature=0.7,
                convert_system_message_to_human=True,
                api_key='AIzaSyCzpzTqKGTd5SVzmpCdZmaj2eFMiUR-nsI'
            )

    except ImportError as e:
        print(f"❌ Error importing required libraries: {e}")
        print("Please install required packages: pip install langgraph langchain-google-genai")
        return

    # Define Brand model INSIDE the function after imports
    class Brand(BaseModel):
        brand_name: str
        brand_niche: str
        brand_values: list[str]
        target_audience: list[str]
        expectation: list[str]

    class MailState(TypedDict):
        brand: Brand
        messages: Annotated[list, add_messages]
        email_content: str
        user_feedback: str
        iteration_count: int
        approval_status: str 

    @tool
    def save_email(content: str) -> str:
        """Save the email content to a file"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"cold_email_{timestamp}.txt"
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return f"Email saved successfully to {filename}"
        except Exception as e:
            return f"Error saving email: {str(e)}"

    @tool
    def analyze_email(content: str) -> str:
        """Analyze email for key metrics and provide suggestions"""
        try:
            word_count = len(content.split())
            has_personalization = '[name]' in content.lower() or 'dear' in content.lower()
            has_cta = any(word in content.lower() for word in ['click', 'schedule', 'call', 'reply', 'book'])
            
            analysis = f"""
EMAIL ANALYSIS:
- Word Count: {word_count} (Ideal: 50-125 words)
- Personalization: {'✓' if has_personalization else '✗'}
- Clear CTA: {'✓' if has_cta else '✗'}
- Length Rating: {'Good' if 50 <= word_count <= 125 else 'Too Long' if word_count > 125 else 'Too Short'}

SUGGESTIONS:
{('- Consider shortening the email' if word_count > 125 else '- Consider adding more value proposition' if word_count < 50 else '- Length is optimal')}
{('- Add personalization elements' if not has_personalization else '')}
{('- Include a clear call-to-action' if not has_cta else '')}
            """
            return analysis.strip()
        except Exception as e:
            return f"Error analyzing email: {str(e)}"

    def generate_email(state: MailState) -> dict:
        """Generate a cold email based on brand information"""
        try:
            llm = get_llm_local()
            brand = state['brand']
            iteration = state.get('iteration_count', 0)
            feedback = state.get('user_feedback', '')
            
            system_prompt = f"""You are a world-class cold email specialist with proven results.
Your emails have high open rates (35%+) and response rates (12%+).

BRAND CONTEXT:
- Brand: {brand.brand_name}
- Niche: {brand.brand_niche} 
- Values: {', '.join(brand.brand_values)}
- Target Audience: {', '.join(brand.target_audience)}
- Customer Expectations: {', '.join(brand.expectation)}

COLD EMAIL REQUIREMENTS:
1. Keep it under 100 words
2. Start with a personalized hook
3. Clearly state the value proposition
4. Include social proof or credibility
5. End with a simple, clear call-to-action
6. Use conversational tone
7. Avoid sales-y language

{f'PREVIOUS FEEDBACK TO INCORPORATE: {feedback}' if feedback else ''}

Generate a compelling cold email that would make the recipient want to reply.
Include a subject line at the beginning."""

            human_message = HumanMessage(content=f"Generate a cold email for {brand.brand_name} targeting {', '.join(brand.target_audience)}")
            
            response = llm.invoke([SystemMessage(content=system_prompt), human_message])
            email_content = response.content
            
            print("\n" + "="*60)
            print("GENERATED EMAIL:")
            print("="*60)
            print(email_content)
            print("="*60)
            
            return {
                "email_content": email_content,
                "messages": [AIMessage(content=f"Generated cold email (iteration {iteration + 1})")]
            }
            
        except Exception as e:
            return {
                "email_content": f"Error generating email: {str(e)}",
                "messages": [AIMessage(content=f"Error in email generation: {str(e)}")]
            }

    def get_user_approval(state: MailState) -> dict:
        """Get user approval or feedback on the generated email"""
        try:
            email_content = state.get('email_content', '')
            
            if not email_content or email_content.startswith('Error'):
                return {
                    "approval_status": "needs_revision",
                    "user_feedback": "Previous generation failed, please retry",
                    "iteration_count": state.get('iteration_count', 0) + 1
                }
            
            analysis = analyze_email(email_content)
            print(f"\n{analysis}")
            
            print("\nOPTIONS:")
            print("1. Approve and save")
            print("2. Request revisions")
            print("3. Generate new version")
            print("4. Exit")
            
            while True:
                try:
                    choice = input("\nYour choice (1-4): ").strip()
                    
                    if choice == '1':
                        save_result = save_email(email_content)
                        print(f"\n✅ {save_result}")
                        return {
                            "approval_status": "approved",
                            "messages": [HumanMessage(content="Email approved and saved")]
                        }
                        
                    elif choice == '2':
                        feedback = input("What would you like to change?: ").strip()
                        return {
                            "approval_status": "needs_revision", 
                            "user_feedback": feedback,
                            "iteration_count": state.get('iteration_count', 0) + 1,
                            "messages": [HumanMessage(content=f"Revision requested: {feedback}")]
                        }
                        
                    elif choice == '3':
                        return {
                            "approval_status": "needs_revision",
                            "user_feedback": "Generate a completely new version",
                            "iteration_count": state.get('iteration_count', 0) + 1,
                            "messages": [HumanMessage(content="New version requested")]
                        }
                        
                    elif choice == '4':
                        return {
                            "approval_status": "rejected",
                            "messages": [HumanMessage(content="Process cancelled by user")]
                        }
                        
                    else:
                        print("Please enter 1, 2, 3, or 4")
                        
                except KeyboardInterrupt:
                    return {
                        "approval_status": "rejected",
                        "messages": [HumanMessage(content="Process interrupted")]
                    }
                    
        except Exception as e:
            return {
                "approval_status": "rejected",
                "messages": [HumanMessage(content=f"Error in approval process: {str(e)}")]
            }

    def route_next_action(state: MailState) -> Literal["generate_email", "user_approval", "tools", "end"]:
        """Route to the next action based on current state"""
        approval_status = state.get('approval_status', 'pending')
        iteration_count = state.get('iteration_count', 0)
        
        # Prevent infinite loops
        if iteration_count > 5:
            print("Maximum iterations reached. Ending process.")
            return "end"
        
        if approval_status == "approved":
            return "end"
        elif approval_status == "rejected":
            return "end"  
        elif approval_status == "needs_revision":
            return "generate_email"
        elif state.get('email_content'):
            return "user_approval"
        else:
            return "generate_email"

    def collect_brand_info() -> Brand:
        """Collect brand information from user"""
        print("\n" + "="*60)
        print("BRAND INFORMATION COLLECTION")
        print("="*60)
        
        brand_name = input("Brand Name: ").strip()
        brand_niche = input("Brand Niche/Industry: ").strip()
        
        print("\nEnter brand values (comma separated):")
        values_input = input("Values: ").strip()
        values = [v.strip() for v in values_input.split(',') if v.strip()] if values_input else []
        
        print("\nEnter target audience (comma separated):")
        audience_input = input("Target Audience: ").strip()
        audience = [a.strip() for a in audience_input.split(',') if a.strip()] if audience_input else []
        
        print("\nEnter customer expectations (comma separated):")
        expectations_input = input("Expectations: ").strip()
        expectations = [e.strip() for e in expectations_input.split(',') if e.strip()] if expectations_input else []
        
        return Brand(
            brand_name=brand_name,
            brand_niche=brand_niche,
            brand_values=values,
            target_audience=audience,
            expectation=expectations
        )

    # Build the workflow graph
    def create_workflow():
        try:
            graph = StateGraph(MailState)
            
            # Add nodes
            graph.add_node("generate_email", generate_email)
            graph.add_node("user_approval", get_user_approval)
            
            # Add tool node
            tools = [save_email, analyze_email]
            tool_node = ToolNode(tools)
            graph.add_node("tools", tool_node)
            
            # Add conditional routing from START
            graph.add_conditional_edges(
                START,
                route_next_action,
                {
                    "generate_email": "generate_email",
                    "user_approval": "user_approval",
                    "tools": "tools",
                    "end": END
                }
            )
            
            # Add conditional edges from generate_email
            graph.add_conditional_edges(
                "generate_email",
                route_next_action,
                {
                    "user_approval": "user_approval",
                    "generate_email": "generate_email",
                    "end": END
                }
            )
            
            # Add conditional edges from user_approval
            graph.add_conditional_edges(
                "user_approval",
                route_next_action,
                {
                    "generate_email": "generate_email",
                    "tools": "tools",
                    "end": END
                }
            )
            
            # Add edge from tools back to routing
            graph.add_conditional_edges(
                "tools",
                route_next_action,
                {
                    "generate_email": "generate_email",
                    "user_approval": "user_approval",
                    "end": END
                }
            )
            
            checkpointer = InMemorySaver()
            return graph.compile(checkpointer=checkpointer)
            
        except Exception as e:
            print(f"❌ Error creating workflow: {e}")
            return None

    # Main execution logic
    try:
        print("="*70)
        print("🚀 COLD EMAIL AI AGENT")
        print("="*70)
        
        workflow = create_workflow()
        if not workflow:
            print("❌ Failed to create workflow. Exiting.")
            return
        
        while True:
            try:
                print("\n" + "="*50)
                brand_input = input("Enter 'start' to begin or 'exit' to quit: ").strip().lower()
                
                if brand_input == 'exit':
                    print("👋 Goodbye!")
                    break
                elif brand_input == 'start':
                    # Collect brand information
                    try:
                        brand = collect_brand_info()
                        
                        # Create initial state
                        initial_state = {
                            "brand": brand,
                            "messages": [],
                            "email_content": "",
                            "user_feedback": "",
                            "iteration_count": 0,
                            "approval_status": "pending"
                        }
                        
                        # Generate session ID
                        thread_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        
                        print(f"\n🎯 Starting email generation for {brand.brand_name}...")
                        
                        # Run the workflow
                        result = workflow.invoke(
                            initial_state,
                            config={"configurable": {"thread_id": thread_id}}
                        )
                        
                        # Display final result
                        final_status = result.get('approval_status', 'unknown')
                        if final_status == 'approved':
                            print("\n✅ Email generation completed successfully!")
                        elif final_status == 'rejected':
                            print("\n❌ Email generation cancelled.")
                        else:
                            print(f"\n⚠️  Process ended with status: {final_status}")
                            
                    except Exception as e:
                        print(f"❌ Error during brand collection or workflow execution: {e}")
                        continue
                        
                else:
                    print("Please enter 'start' or 'exit'")
                with open('email.txt','w') as f:
                    f.write(result['email_content'])
                    f.write("\n\n---\n\n")
            except KeyboardInterrupt:
                print("\n\n👋 Process interrupted. Goodbye!")
                break
                
    except Exception as e:
        print(f"❌ Fatal error in email agent: {e}")


# For backward compatibility, keep the main execution
def main():
    run_email_agent()

if __name__ == "__main__":
    main()