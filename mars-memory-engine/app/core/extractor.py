"""
Rule-based Memory Extractor for MARS Memory Engine.

Extracts candidate memories (decision, fact, procedure, risk) from discussion windows
using deterministic rules instead of LLM calls.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class RuleBasedExtractor:
    """Extracts candidate memories using rule-based patterns."""

    # Decision keywords - Chinese
    DECISION_PATTERNS_CN = [
        r"决定用|决定采用|决定使用|决定不用|决定不用",
        r"就用|我们用|那就用|改成|换成|改为",
        r"确定用|选定|采用|使用.*方案|选.*方案",
        r"好，|好的，|行，|可以，|那就",
        r"先.*再说|一期.*|正式版.*",
        r"作废|取消|推翻|重新",
    ]

    # Decision keywords - English
    DECISION_PATTERNS_EN = [
        r"\bdecid(e|ed|esion)\b",
        r"\bagree(d|r|eement)?\b",
        r"\bchose?\b",
        r"\bchosen\b",
        r"\brecommen(d|ded|dation)\b",
        r"\bsugges(t|ted|tion)\b",
        r"\bshould\b",
        r"\bneed to\b",
        r"\bgoing to\b",
        r"\bincorporat(e|ed|ion)\b",
        r"\bgive up\b",
        r"\bgave up\b",
        r"\buse\b|\busing\b",
        r"\bintegrate\b",
        r"\binclude\b",
        r"\bkeep\b",
        r"\bremain\b",
        r"\bchoose\b|\bchoosing\b",
        r"\bwill\b",
        r"\bwant\b|\bwants\b",
        r"\bprefer\b|\bprefers?\b",
    ]

    @property
    def DECISION_PATTERNS(self):
        """Get combined decision patterns."""
        return self.DECISION_PATTERNS_CN + self.DECISION_PATTERNS_EN

    # Fact keywords - Chinese
    FACT_PATTERNS_CN = [
        r"目前|现在|已经|完成|实现",
        r"版本|v\d+\.\d+|release",
        r"上线|发布|部署",
    ]

    # Fact keywords - English
    FACT_PATTERNS_EN = [
        r"\bmarket research\b",
        r"\busers?\b",
        r"\bpercent\b",
        r"\brequirement\b|\brequirements\b",
        r"\bexpect(ed|s|ation)?\b",
        r"\bfound\b",
        r"\bpresented?\b",
        r"\bshowed?\b",
        r"\bindicates?\b",
        r"\bresult\b",
    ]

    @property
    def FACT_PATTERNS(self):
        """Get combined fact patterns."""
        return self.FACT_PATTERNS_CN + self.FACT_PATTERNS_EN

    # Procedure keywords - Chinese
    PROCEDURE_PATTERNS_CN = [
        r"流程|步骤|怎么|如何",
        r"先.*然后|第一步|首先|接下来",
        r"需要.*才能|必须.*然后",
    ]

    # Procedure keywords - English
    PROCEDURE_PATTERNS_EN = [
        r"\bprocess\b",
        r"\bstep\b",
        r"\bhow to\b",
        r"\bfirst\b",
        r"\bthen\b",
        r"\bnext\b",
        r"\bafter\b",
        r"\bbefore\b",
        r"\bneed to\b",
        r"\bmust\b",
    ]

    @property
    def PROCEDURE_PATTERNS(self):
        """Get combined procedure patterns."""
        return self.PROCEDURE_PATTERNS_CN + self.PROCEDURE_PATTERNS_EN

    # Risk keywords - Chinese
    RISK_PATTERNS_CN = [
        r"问题|风险|担心|怕",
        r"如果.*怎么办|万一.*呢",
        r"注意|小心|需要考虑",
        r"不确定|不确定",
    ]

    # Risk keywords - English
    RISK_PATTERNS_EN = [
        r"\bcost\b|\bcosts\b|\bcostly\b|\bcosting\b",
        r"\bexpens(ive|e)?\b",
        r"\bproblem\b|\bproblems\b",
        r"\bissue\b|\bissues\b",
        r"\bconcern\b|\bconcerns?\b",
        r"\bcomplicat(ed|es?|ion)?\b",
        r"\btoo many buttons\b",
        r"\bnot user-friendly\b",
        r"\bnot user friendly\b",
        r"\bdifficult\b",
        r"\blost\b",
        r"\bfrustration\b|\bfrustrat(e|es|ed|ing)\b",
        r"\bworr(y|ies|ying)\b",
        r"\brisk\b|\brisks\b",
        r"\bneed to consider\b",
        r"\bshould be careful\b",
        r"\buncertain\b|\buncertainty\b",
    ]

    @property
    def RISK_PATTERNS(self):
        """Get combined risk patterns."""
        return self.RISK_PATTERNS_CN + self.RISK_PATTERNS_EN

    # Topic inference keywords
    TOPIC_KEYWORDS = {
        "技术路线": ["技术", "架构", "框架", "stack", "技术栈", "前端", "后端", "语言", "语言"],
        "产品功能": ["功能", "需求", "产品", "用户", "体验"],
        "排期计划": ["排期", "时间", "截止", "deadline", "日期", "周"],
        "API设计": ["api", "接口", "文档", "endpoint", "调用"],
        "测试": ["测试", "qa", "bug", "用例", "验收"],
        "部署": ["部署", "上线", "发布", "release", "环境"],
        "数据": ["数据", "数据库", "存储", "表", "字段"],
        "UI设计": ["ui", "界面", "页面", "布局", "样式"],
        # English topics for QMSum
        "Remote Control": ["remote control", "controller", "remote"],
        "Design": ["design", "designer", "stylish", "style"],
        "Market Research": ["market research", "research", "marketing"],
        "Interface": ["interface", "user interface", "ui", "user-friendly"],
        "Material": ["material", "plastic", "metal", "lightweight"],
        "Buttons": ["button", "buttons", "basic buttons"],
        "Display": ["display", "menu display", "screen", "panel", "touch screen"],
        "Speech Recognition": ["speech recognition", "voice recognition", "speech"],
        "Alarm": ["alarm", "detection", "locate"],
        "Cost": ["cost", "expensive", "budget", "price"],
    }

    def __init__(self):
        """Initialize the rule-based extractor."""
        # Compile regex patterns
        self.decision_regex = re.compile("|".join(self.DECISION_PATTERNS), re.IGNORECASE)
        self.fact_regex = re.compile("|".join(self.FACT_PATTERNS), re.IGNORECASE)
        self.procedure_regex = re.compile("|".join(self.PROCEDURE_PATTERNS), re.IGNORECASE)
        self.risk_regex = re.compile("|".join(self.RISK_PATTERNS), re.IGNORECASE)

    def extract_from_window(
        self,
        window: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract candidate memories from a discussion window.

        Args:
            window: Window dict with event_ids.
            events: Full event details for the window.

        Returns:
            List of candidate memory dicts.
        """
        candidates = []
        project_id = window.get("project_id", "")

        # Combine all content for analysis
        combined_content = " ".join([
            e.get("content", "") for e in events
        ])

        # Extract decision
        decision = self._extract_decision(combined_content, events, project_id)
        if decision:
            candidates.append(decision)

        # Extract fact
        fact = self._extract_fact(combined_content, events, project_id)
        if fact:
            candidates.append(fact)

        # Extract procedure
        procedure = self._extract_procedure(combined_content, events, project_id)
        if procedure:
            candidates.append(procedure)

        # Extract risk
        risk = self._extract_risk(combined_content, events, project_id)
        if risk:
            candidates.append(risk)

        return candidates

    def _extract_decision(
        self,
        content: str,
        events: List[Dict[str, Any]],
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Extract a decision candidate from content.

        Args:
            content: Combined content text.
            events: Event details.
            project_id: Project ID.

        Returns:
            Decision candidate dict or None.
        """
        # Supersede patterns - prioritize messages with these
        SUPERSEDE_PATTERNS_CN = [
            r"改成|换成|改为|作废|不用|不再|取消|放弃|推翻",
        ]
        SUPERSEDE_PATTERNS_EN = [
            r"\bchange\b",
            r"\breplace\b",
            r"\binstead\b",
            r"\bgive up\b",
            r"\bgave up\b",
            r"\brefuse\b|\brefused\b",
            r"\bnot use\b",
            r"\bno longer\b",
            r"\bcancel\b",
            r"\breject\b",
            r"\breplace\b",
        ]
        SUPERSEDE_PATTERNS = SUPERSEDE_PATTERNS_CN + SUPERSEDE_PATTERNS_EN
        supersede_regex = re.compile("|".join(SUPERSEDE_PATTERNS), re.IGNORECASE)

        # Find decision-making messages
        decision_messages = []
        for event in events:
            if self.decision_regex.search(event.get("content", "")):
                decision_messages.append(event)

        if not decision_messages:
            return None

        # Get the key decision message
        # Prioritize messages with supersede keywords
        key_msg = None
        for msg in reversed(decision_messages):  # Check from newest to oldest
            if supersede_regex.search(msg.get("content", "")):
                key_msg = msg
                break

        # If no supersede message found, use the last decision message
        if key_msg is None:
            key_msg = decision_messages[-1]

        key_content = key_msg.get("content", "")

        # Infer topic
        topic = self._infer_topic(content)

        # Create a concise summary
        summary = self._summarize_decision(key_content, topic)

        return {
            "candidate_id": f"cand_{uuid.uuid4().hex[:12]}",
            "candidate_type": "decision",
            "topic": topic,
            "summary": summary,
            "evidence_event_ids": [e["event_id"] for e in decision_messages],
            "project_id": project_id,
            "confidence": 0.8,
            "need_human_confirm": False,
        }

    def _extract_fact(
        self,
        content: str,
        events: List[Dict[str, Any]],
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Extract a fact candidate from content.

        Args:
            content: Combined content text.
            events: Event details.
            project_id: Project ID.

        Returns:
            Fact candidate dict or None.
        """
        fact_messages = []
        for event in events:
            if self.fact_regex.search(event.get("content", "")):
                fact_messages.append(event)

        if not fact_messages:
            return None

        key_msg = fact_messages[0]
        key_content = key_msg.get("content", "")
        topic = self._infer_topic(content)

        return {
            "candidate_id": f"cand_{uuid.uuid4().hex[:12]}",
            "candidate_type": "fact",
            "topic": topic,
            "summary": key_content[:200],
            "evidence_event_ids": [e["event_id"] for e in fact_messages],
            "project_id": project_id,
            "confidence": 0.7,
            "need_human_confirm": False,
        }

    def _extract_procedure(
        self,
        content: str,
        events: List[Dict[str, Any]],
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Extract a procedure candidate from content.

        Args:
            content: Combined content text.
            events: Event details.
            project_id: Project ID.

        Returns:
            Procedure candidate dict or None.
        """
        procedure_messages = []
        for event in events:
            if self.procedure_regex.search(event.get("content", "")):
                procedure_messages.append(event)

        if not procedure_messages:
            return None

        key_msg = procedure_messages[0]
        key_content = key_msg.get("content", "")

        return {
            "candidate_id": f"cand_{uuid.uuid4().hex[:12]}",
            "candidate_type": "procedure",
            "topic": "工作流程",
            "summary": key_content[:200],
            "evidence_event_ids": [e["event_id"] for e in procedure_messages],
            "project_id": project_id,
            "confidence": 0.6,
            "need_human_confirm": True,
        }

    def _extract_risk(
        self,
        content: str,
        events: List[Dict[str, Any]],
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Extract a risk candidate from content.

        Args:
            content: Combined content text.
            events: Event details.
            project_id: Project ID.

        Returns:
            Risk candidate dict or None.
        """
        risk_messages = []
        for event in events:
            if self.risk_regex.search(event.get("content", "")):
                risk_messages.append(event)

        if not risk_messages:
            return None

        key_msg = risk_messages[0]
        key_content = key_msg.get("content", "")

        return {
            "candidate_id": f"cand_{uuid.uuid4().hex[:12]}",
            "candidate_type": "risk",
            "topic": "风险关注点",
            "summary": key_content[:200],
            "evidence_event_ids": [e["event_id"] for e in risk_messages],
            "project_id": project_id,
            "confidence": 0.5,
            "need_human_confirm": True,
        }

    def _infer_topic(self, content: str) -> str:
        """Infer topic from content using keywords.

        Args:
            content: Content text.

        Returns:
            Topic string.
        """
        content_lower = content.lower()

        for topic, keywords in self.TOPIC_KEYWORDS.items():
            if any(kw in content_lower for kw in keywords):
                return topic

        return "General Discussion"

    def _summarize_decision(self, content: str, topic: str) -> str:
        """Create a concise summary of a decision.

        Args:
            content: Decision message content.
            topic: Topic category.

        Returns:
            Summary string.
        """
        # Remove common prefixes (Chinese and English)
        clean = re.sub(r"^(好，|好的，|行，|可以，|那就|okay|alright|so )", "", content.strip(), flags=re.IGNORECASE)

        # Limit length - increased to 300 to preserve supersede information
        if len(clean) > 300:
            # Try to end at a sentence boundary
            for end_marker in ['。', '！', '？', '.', '!', '?']:
                last_pos = clean.rfind(end_marker, 0, 300)
                if last_pos > 200:  # Only use sentence boundary if it's not too short
                    clean = clean[:last_pos + 1]
                    break
            else:
                clean = clean[:300] + "..."

        return clean.strip()

    def extract_candidates(
        self,
        windows: List[Dict[str, Any]],
        events_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract candidates from multiple windows.

        Args:
            windows: List of window dicts.
            events_map: Map of event_id to event details.

        Returns:
            List of all candidate memories.
        """
        all_candidates = []

        for window in windows:
            event_ids = window.get("event_ids", [])
            events = [events_map[eid] for eid in event_ids if eid in events_map]

            if not events:
                continue

            candidates = self.extract_from_window(window, events)
            all_candidates.extend(candidates)

        return all_candidates


class LLMExtractorPlaceholder:
    """Placeholder for future LLM-based extraction.

    This class provides the same interface as RuleBasedExtractor
    but can be swapped out for an LLM-based implementation later.
    """

    def extract_from_window(
        self,
        window: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Placeholder for LLM extraction.

        Args:
            window: Window dict.
            events: Event details.

        Returns:
            Empty list (placeholder).
        """
        # TODO: Implement LLM-based extraction
        return []

    def extract_candidates(
        self,
        windows: List[Dict[str, Any]],
        events_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Placeholder for LLM extraction.

        Args:
            windows: List of window dicts.
            events_map: Map of event_id to event details.

        Returns:
            Empty list (placeholder).
        """
        # TODO: Implement LLM-based extraction
        return []
