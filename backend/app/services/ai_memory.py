"""Conversation memory for AI assistant - stores context and historical references."""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.ai import AiConversation, AiMessage


class ConversationMemory:
    """Manages conversation context and historical references for AI assistant."""
    
    def __init__(self):
        self.memory: Dict[int, Dict[str, Any]] = {}  # conversation_id -> context
        self.max_context_items = 10  # Keep last 10 exchanges
        self.max_age_hours = 24  # Context expires after 24 hours
    
    def get_context(self, conversation_id: int, db: Session) -> Dict[str, Any]:
        """Retrieve conversation context for follow-up resolution."""
        if conversation_id is None:
            return {}
        
        conv = db.scalar(select(AiConversation).where(AiConversation.id == conversation_id))
        if not conv:
            return {}
        
        # Get recent messages for context
        messages = db.scalars(
            select(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .order_by(AiMessage.id.desc())
            .limit(self.max_context_items)
        ).all()
        
        # Build context dictionary
        context = {
            "conversation_id": conversation_id,
            "title": conv.title,
            "recent_queries": [],
            "last_topic": None,
            "entities": {},
        }
        
        for msg in reversed(messages):  # Oldest first
            if msg.role == "user":
                context["recent_queries"].append({
                    "message": msg.content,
                    "timestamp": msg.created_at,
                    "filters": msg.filters or [],
                    "item_ids": msg.item_ids or [],
                })
                # Extract entities from the last query
                if not context["last_topic"]:
                    context["last_topic"] = self._extract_topic(msg.content)
        
        return context
    
    def update_context(self, conversation_id: int, user_message: str, ai_response: str, 
                      filters: list, item_ids: list, db: Session) -> None:
        """Store conversation exchange in memory and database."""
        if conversation_id is None:
            return
        
        # Store in database for persistence
        db.add(AiMessage(
            conversation_id=conversation_id,
            role="user",
            content=user_message,
            filters=filters,
            item_ids=item_ids
        ))
        db.add(AiMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
            filters=[],
            item_ids=[]
        ))
        db.commit()
        
        # Update in-memory context
        if conversation_id not in self.memory:
            self.memory[conversation_id] = {"queries": [], "entities": {}}
        
        self.memory[conversation_id]["queries"].append({
            "user": user_message,
            "ai": ai_response,
            "timestamp": datetime.utcnow(),
            "filters": filters,
            "item_ids": item_ids,
        })
        
        # Keep only recent items
        if len(self.memory[conversation_id]["queries"]) > self.max_context_items:
            self.memory[conversation_id]["queries"] = self.memory[conversation_id]["queries"][-self.max_context_items:]
    
    def resolve_follow_up(self, current_query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve follow-up references like "last month", "that order", etc."""
        if not context or not context["recent_queries"]:
            return {"resolved": False, "query": current_query}
        
        last_query = context["recent_queries"][-1]["message"].lower()
        current_lower = current_query.lower()
        
        resolved = {
            "resolved": False,
            "query": current_query,
            "reference": None,
            "period_changed": False,
        }
        
        # Detect period changes
        if "last month" in current_lower and context["last_topic"]:
            resolved["period_changed"] = True
            resolved["reference"] = "previous period"
            resolved["resolved"] = True
        elif "this month" in current_lower and context["last_topic"]:
            resolved["period_changed"] = False
            resolved["reference"] = "current period"
            resolved["resolved"] = True
        elif "last week" in current_lower:
            resolved["period_changed"] = True
            resolved["reference"] = "previous week"
            resolved["resolved"] = True
        elif "this week" in current_lower:
            resolved["period_changed"] = False
            resolved["reference"] = "current week"
            resolved["resolved"] = True
        elif "that order" in current_lower or "the order" in current_lower:
            if context["recent_queries"][-1].get("item_ids"):
                resolved["reference"] = "previous order"
                resolved["resolved"] = True
            elif context["recent_queries"][-1].get("filters"):
                resolved["reference"] = "previous filters"
                resolved["resolved"] = True
        
        return resolved
    
    def _extract_topic(self, message: str) -> str:
        """Extract the main topic from a message."""
        words = message.lower().split()
        topic_keywords = ["earnings", "revenue", "orders", "deliveries", "sales", "money", "commission"]
        for word in words:
            if word in topic_keywords:
                return word
        return "general"


# Global conversation memory instance
conversation_memory = ConversationMemory()
