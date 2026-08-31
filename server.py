import os
import json
import time
import socket
from typing import List
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- Mac / IPv6 Hang Fix ---
old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(*args, **kwargs):
    responses = old_getaddrinfo(*args, **kwargs)
    return [res for res in responses if res[0] == socket.AF_INET]
socket.getaddrinfo = new_getaddrinfo

# Import the models from Code
from Code import CandidateProfile, AgentEvaluation, DebateResponse, FinalDecision, MODEL

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    with open("static/index.html", "r") as f:
        return HTMLResponse(content=f.read())

def process_candidate_stream(candidate_name, jd_path, resume_path, transcript_path, api_key):
    client = genai.Client(api_key=api_key)
    
    def emit(event, data=""):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"
        
    try:
        yield emit("progress", f"Uploading {candidate_name} documents to Gemini...")
        jd_file = client.files.upload(file=jd_path)
        resume_file = client.files.upload(file=resume_path)
        transcript_file = client.files.upload(file=transcript_path)
        
        # Profile
        yield emit("progress", "Building Candidate Profile...")
        prompt = """
        You are a Candidate Profile Builder. Your job is to read the provided Job Description, Resume, and Interview Transcript, 
        and extract the basic facts about the candidate. 
        """
        response = client.models.generate_content(
            model=MODEL,
            contents=["Job Description:", jd_file, "Resume:", resume_file, "Transcript:", transcript_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CandidateProfile,
                temperature=0.2,
            ),
        )
        profile = CandidateProfile.model_validate_json(response.text)
        yield emit("progress", f"Profile built! {profile.experience_years} years experience.")
        
        # Evals
        personas = {
            "Technical Agent": "Check technical skill and depth. Verify if the technical experience matches the job requirements.",
            "HR / Culture Agent": "Check communication, teamwork, and honesty. Look for signs of good cultural fit and interpersonal skills.",
            "Hiring Manager Agent": "Check if this person is worth hiring for the role overall, balancing technical skills with potential and team fit.",
            "Skeptic Agent": "Look for contradictions, exaggeration, red flags, or inconsistencies in their resume vs what they said in the interview."
        }
        
        evaluations = []
        for p_name, p_inst in personas.items():
            yield emit("progress", f"Evaluating as {p_name}...")
            prompt_eval = f"""
            You are the {p_name}. 
            Your Role Instructions: {p_inst}
            
            Candidate Profile Summary: {profile.model_dump_json(indent=2)}
            
            Evaluate the candidate based on the Job Description, Resume, and Transcript provided.
            You MUST provide exact quotes from the resume or transcript to back up your strengths and concerns.
            Do NOT see what other agents have said (this is an independent evaluation).
            """
            res_eval = client.models.generate_content(
                model=MODEL,
                contents=["Job Description:", jd_file, "Resume:", resume_file, "Transcript:", transcript_file, prompt_eval],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AgentEvaluation,
                    temperature=0.4,
                ),
            )
            evaluations.append(AgentEvaluation.model_validate_json(res_eval.text))
            
        # Debate
        debates = []
        for i, (p_name, p_inst) in enumerate(personas.items()):
            yield emit("progress", f"Running Debate Step for {p_name}...")
            this_eval = evaluations[i]
            other_evals = evaluations[:i] + evaluations[i+1:]
            
            prompt_deb = f"""
            You are the {p_name}.
            Your Role Instructions: {p_inst}
            
            Your initial independent evaluation was:
            {this_eval.model_dump_json(indent=2)}
            
            The other agents have evaluated the candidate as well. Here are their evaluations:
            {json.dumps([e.model_dump() for e in other_evals], indent=2)}
            
            Task: Review the other agents' evaluations. You MUST directly respond to at least one point made by another agent. 
            State what you agree with, what you disagree with, and why. 
            Finally, state whether your stance has changed based on this debate.
            """
            res_deb = client.models.generate_content(
                model=MODEL,
                contents=[prompt_deb],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DebateResponse,
                    temperature=0.5,
                ),
            )
            debates.append(DebateResponse.model_validate_json(res_deb.text))
            
        # Final Decision
        yield emit("progress", "Synthesizing Final Hiring Decision...")
        prompt_fin = f"""
        You are the Final Hiring Committee Chair. 
        You have received independent evaluations from 4 specialized agents, as well as their responses during the debate stage.
        
        Initial Evaluations:
        {json.dumps([e.model_dump() for e in evaluations], indent=2)}
        
        Debate Responses:
        {json.dumps([d.model_dump() for d in debates], indent=2)}
        
        Your job is to synthesize all this information and make a final hiring decision. 
        Do NOT just mathematically average the scores. You must weigh the evidence, the quotes provided, 
        and how the agents resolved (or failed to resolve) their disagreements during the debate.
        """
        res_fin = client.models.generate_content(
            model=MODEL,
            contents=[prompt_fin],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FinalDecision,
                temperature=0.3,
            ),
        )
        decision = FinalDecision.model_validate_json(res_fin.text)
        
        # Report
        from Code import generate_report
        report_md = generate_report(candidate_name, profile, evaluations, debates, decision)
        
        # Cleanup
        client.files.delete(name=jd_file.name)
        client.files.delete(name=resume_file.name)
        client.files.delete(name=transcript_file.name)
        
        yield emit("done", report_md)
        
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        yield emit("error", str(e))
        print("Error:", error_msg)

@app.post("/api/evaluate")
def api_evaluate(
    candidate_name: str = Form(...),
    api_key: str = Form(...),
    jd: UploadFile = File(...),
    resume: UploadFile = File(...),
    transcript: UploadFile = File(...)
):
    # Save files locally
    jd_path = f"tmp_jd_{jd.filename}"
    resume_path = f"tmp_resume_{resume.filename}"
    transcript_path = f"tmp_transcript_{transcript.filename}"
    
    with open(jd_path, "wb") as f: f.write(jd.file.read())
    with open(resume_path, "wb") as f: f.write(resume.file.read())
    with open(transcript_path, "wb") as f: f.write(transcript.file.read())
    
    def cleanup_and_stream():
        try:
            yield from process_candidate_stream(candidate_name, jd_path, resume_path, transcript_path, api_key)
        finally:
            if os.path.exists(jd_path): os.remove(jd_path)
            if os.path.exists(resume_path): os.remove(resume_path)
            if os.path.exists(transcript_path): os.remove(transcript_path)
            
    return StreamingResponse(cleanup_and_stream(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
