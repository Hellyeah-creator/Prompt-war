import os
import json
from typing import List, Dict
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Define Pydantic Models for Structured Output

class CandidateProfile(BaseModel):
    skills: List[str] = Field(description="List of technical and soft skills extracted.")
    experience_years: int = Field(description="Estimated total years of experience.")
    core_claims: List[str] = Field(description="Key claims made by the candidate in the resume and transcript.")
    summary: str = Field(description="A brief summary of the candidate's background.")

class AgentEvaluation(BaseModel):
    persona_name: str = Field(description="Name of the evaluating persona.")
    strengths: List[str] = Field(description="Candidate's strengths according to this persona. MUST be backed by quotes.")
    concerns: List[str] = Field(description="Candidate's concerns or red flags according to this persona. MUST be backed by quotes.")
    quotes_used: List[str] = Field(description="Exact quotes from the transcript or resume used to back up the evaluation.")
    confidence_score: int = Field(description="Confidence score from 1 to 10 on the evaluation.")
    hire_recommendation: str = Field(description="Yes, No, or Maybe.")
    reasoning: str = Field(description="Detailed reasoning for the recommendation.")

class DebateResponse(BaseModel):
    persona_name: str = Field(description="Name of the persona giving this debate response.")
    agreements: List[str] = Field(description="Points from other agents that this agent agrees with.")
    disagreements: List[str] = Field(description="Points from other agents that this agent disagrees with, and why. MUST directly respond to another agent's point.")
    updated_stance: str = Field(description="Has the agent's stance changed? If so, how? What is their updated hire recommendation (Yes, No, Maybe)?")

class FinalDecision(BaseModel):
    final_recommendation: str = Field(description="Hire or Do Not Hire.")
    confidence_level: str = Field(description="High, Medium, or Low.")
    key_strengths: List[str] = Field(description="Consolidated strengths of the candidate.")
    key_concerns: List[str] = Field(description="Consolidated concerns or red flags.")
    unresolved_disagreements: List[str] = Field(description="Any major disagreements between agents that were not fully resolved.")
    reasoning: str = Field(description="Detailed final reasoning weighing the evidence from the debate.")

# API Client Initialization
# Ensure GEMINI_API_KEY is set in your environment variables.
client = None
MODEL = "gemini-3.5-flash-lite"

def upload_pdf(file_path: str):
    print(f"Uploading {file_path} to Gemini...")
    return client.files.upload(file=file_path)

def build_profile(jd_file, resume_file, transcript_file) -> CandidateProfile:
    print("Building Candidate Profile...")
    prompt = """
    You are a Candidate Profile Builder. Your job is to read the provided Job Description, Resume, and Interview Transcript, 
    and extract the basic facts about the candidate. 
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            "Job Description:", jd_file,
            "Resume:", resume_file,
            "Transcript:", transcript_file,
            prompt
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CandidateProfile,
            temperature=0.2,
        ),
    )
    return CandidateProfile.model_validate_json(response.text)

def evaluate_candidate(jd_file, resume_file, transcript_file, profile: CandidateProfile, persona: str, instructions: str) -> AgentEvaluation:
    print(f"Evaluating as {persona}...")
    
    prompt = f"""
    You are the {persona}. 
    Your Role Instructions: {instructions}
    
    Candidate Profile Summary: {profile.model_dump_json(indent=2)}
    
    Evaluate the candidate based on the Job Description, Resume, and Transcript provided.
    You MUST provide exact quotes from the resume or transcript to back up your strengths and concerns.
    Do NOT see what other agents have said (this is an independent evaluation).
    """
    
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            "Job Description:", jd_file,
            "Resume:", resume_file,
            "Transcript:", transcript_file,
            prompt
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AgentEvaluation,
            temperature=0.4,
        ),
    )
    return AgentEvaluation.model_validate_json(response.text)

def debate_stage(this_eval: AgentEvaluation, other_evals: List[AgentEvaluation], persona: str, persona_instructions: str) -> DebateResponse:
    print(f"Running Debate Step for {persona}...")
    
    other_evaluations_json = json.dumps(
        [eval.model_dump() for eval in other_evals], 
        indent=2
    )
    this_agent_evaluation = this_eval.model_dump_json(indent=2)
    
    prompt = f"""
    You are the {persona}.
    Your Role Instructions: {persona_instructions}
    
    Your initial independent evaluation was:
    {this_agent_evaluation}
    
    The other agents have evaluated the candidate as well. Here are their evaluations:
    {other_evaluations_json}
    
    Task: Review the other agents' evaluations. You MUST directly respond to at least one point made by another agent. 
    State what you agree with, what you disagree with, and why. 
    Finally, state whether your stance has changed based on this debate.
    """
    
    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DebateResponse,
            temperature=0.5,
        ),
    )
    return DebateResponse.model_validate_json(response.text)

def make_final_decision(evaluations: List[AgentEvaluation], debate_responses: List[DebateResponse]) -> FinalDecision:
    print("Making Final Hiring Decision...")
    
    evals_json = json.dumps([e.model_dump() for e in evaluations], indent=2)
    debates_json = json.dumps([d.model_dump() for d in debate_responses], indent=2)
    
    prompt = f"""
    You are the Final Hiring Committee Chair. 
    You have received independent evaluations from 4 specialized agents, as well as their responses during the debate stage.
    
    Initial Evaluations:
    {evals_json}
    
    Debate Responses:
    {debates_json}
    
    Your job is to synthesize all this information and make a final hiring decision. 
    Do NOT just mathematically average the scores. You must weigh the evidence, the quotes provided, 
    and how the agents resolved (or failed to resolve) their disagreements during the debate.
    """
    
    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FinalDecision,
            temperature=0.3,
        ),
    )
    return FinalDecision.model_validate_json(response.text)

def generate_report(candidate_name: str, profile: CandidateProfile, evals: List[AgentEvaluation], debates: List[DebateResponse], decision: FinalDecision):
    report = f"# Final Hiring Report: {candidate_name}\n\n"
    report += f"## Final Recommendation: **{decision.final_recommendation}** (Confidence: {decision.confidence_level})\n\n"
    
    report += "### Candidate Summary\n"
    report += f"{profile.summary}\n\n"
    report += f"**Years of Experience**: {profile.experience_years}\n\n"
    report += f"**Skills**: {', '.join(profile.skills)}\n\n"
    
    report += "### Key Strengths\n"
    for s in decision.key_strengths:
        report += f"- {s}\n"
    report += "\n### Key Concerns\n"
    for c in decision.key_concerns:
        report += f"- {c}\n"
        
    report += "\n### Unresolved Disagreements\n"
    if decision.unresolved_disagreements:
        for d in decision.unresolved_disagreements:
            report += f"- {d}\n"
    else:
        report += "None reported.\n"
        
    report += f"\n### Final Reasoning\n{decision.reasoning}\n\n"
    
    report += "---\n## Appendix: Initial Independent Evaluations\n\n"
    for e in evals:
        report += f"### {e.persona_name} (Score: {e.confidence_score}/10, Rec: {e.hire_recommendation})\n"
        report += f"**Reasoning**: {e.reasoning}\n"
        report += "**Quotes Used**:\n"
        for q in e.quotes_used:
            report += f"- \"{q}\"\n"
        report += "\n"
        
    report += "---\n## Appendix: Debate Stage\n\n"
    for d in debates:
        report += f"### {d.persona_name}\n"
        report += f"**Agreements**: {', '.join(d.agreements) if d.agreements else 'None'}\n"
        report += f"**Disagreements**: {', '.join(d.disagreements) if d.disagreements else 'None'}\n"
        report += f"**Updated Stance**: {d.updated_stance}\n\n"
        
    return report

def process_candidate(candidate_name, jd_path, resume_path, transcript_path):
    print(f"\n========== Processing {candidate_name} ==========")
    jd_file = upload_pdf(jd_path)
    resume_file = upload_pdf(resume_path)
    transcript_file = upload_pdf(transcript_path)
    
    # Stage 1: Profile Building
    profile = build_profile(jd_file, resume_file, transcript_file)
    
    # Stage 2: Independent Evaluation
    personas = {
        "Technical Agent": "Check technical skill and depth. Verify if the technical experience matches the job requirements.",
        "HR / Culture Agent": "Check communication, teamwork, and honesty. Look for signs of good cultural fit and interpersonal skills.",
        "Hiring Manager Agent": "Check if this person is worth hiring for the role overall, balancing technical skills with potential and team fit.",
        "Skeptic Agent": "Look for contradictions, exaggeration, red flags, or inconsistencies in their resume vs what they said in the interview."
    }
    
    evaluations = []
    for p_name, p_inst in personas.items():
        ev = evaluate_candidate(jd_file, resume_file, transcript_file, profile, p_name, p_inst)
        evaluations.append(ev)
        
    # Stage 3: Debate
    debates = []
    for i, (p_name, p_inst) in enumerate(personas.items()):
        this_eval = evaluations[i]
        other_evals = evaluations[:i] + evaluations[i+1:]
        deb = debate_stage(this_eval, other_evals, p_name, p_inst)
        debates.append(deb)
        
    # Stage 4: Final Decision
    decision = make_final_decision(evaluations, debates)
    
    # Stage 5: Final Report
    report = generate_report(candidate_name, profile, evaluations, debates, decision)
    
    print(f"\nFinal Recommendation for {candidate_name}: {decision.final_recommendation}")
    print("Saving report to file...")
    
    filename = f"{candidate_name.replace(' ', '_')}_Report.md"
    with open(filename, "w") as f:
        f.write(report)
    print(f"Saved: {filename}")
    
    # Cleanup files on Google GenAI
    client.files.delete(name=jd_file.name)
    client.files.delete(name=resume_file.name)
    client.files.delete(name=transcript_file.name)

def main():
    global client
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable not set.")
        return
    client = genai.Client()
        
    jd = "02_Job_Description.pdf"
    
    # Process Candidate A
    process_candidate(
        candidate_name="Candidate A",
        jd_path=jd,
        resume_path="03_Resume_A.pdf",
        transcript_path="05_Transcript_A.pdf"
    )
    
    print("\n[Rate Limit] Waiting 60 seconds to avoid Gemini free-tier quota (15 requests per minute)...")
    import time
    time.sleep(60)
    
    # Process Candidate B
    process_candidate(
        candidate_name="Candidate B",
        jd_path=jd,
        resume_path="04_Resume_B.pdf",
        transcript_path="06_Transcript_B.pdf"
    )

if __name__ == "__main__":
    main()
