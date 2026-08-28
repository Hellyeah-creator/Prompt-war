document.getElementById('evaluationForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const form = e.target;
    const submitBtn = document.getElementById('submitBtn');
    const progressContainer = document.getElementById('progressContainer');
    const eventLog = document.getElementById('eventLog');
    const reportContainer = document.getElementById('reportContainer');
    const reportContent = document.getElementById('reportContent');

    // UI State: Loading
    submitBtn.disabled = true;
    submitBtn.textContent = "Processing...";
    form.classList.add('hidden');
    progressContainer.classList.remove('hidden');
    eventLog.innerHTML = "";

    const formData = new FormData(form);

    try {
        const response = await fetch('/api/evaluate', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }

        // Read the Server-Sent Events stream using Fetch API
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            
            // Parse SSE format: event: ... \n data: ... \n\n
            let boundary = buffer.indexOf("\n\n");
            while (boundary !== -1) {
                const chunk = buffer.slice(0, boundary);
                buffer = buffer.slice(boundary + 2);
                
                const lines = chunk.split("\n");
                let eventName = "";
                let eventData = "";

                lines.forEach(line => {
                    if (line.startsWith("event:")) {
                        eventName = line.substring(6).trim();
                    } else if (line.startsWith("data:")) {
                        eventData = JSON.parse(line.substring(5).trim());
                    }
                });

                if (eventName === "progress") {
                    const li = document.createElement("li");
                    li.textContent = eventData;
                    eventLog.appendChild(li);
                    // Scroll to bottom
                    eventLog.scrollTop = eventLog.scrollHeight;
                } 
                else if (eventName === "done") {
                    // Hide progress, show report
                    progressContainer.classList.add('hidden');
                    reportContainer.classList.remove('hidden');
                    
                    // Render markdown using Marked.js
                    reportContent.innerHTML = marked.parse(eventData);
                }
                else if (eventName === "error") {
                    const li = document.createElement("li");
                    li.textContent = "❌ Error: " + eventData;
                    li.style.color = "var(--danger)";
                    eventLog.appendChild(li);
                    document.querySelector('.spinner').style.display = 'none';
                }

                boundary = buffer.indexOf("\n\n");
            }
        }
    } catch (error) {
        console.error(error);
        const li = document.createElement("li");
        li.textContent = "❌ Network Error: " + error.message;
        li.style.color = "var(--danger)";
        eventLog.appendChild(li);
        document.querySelector('.spinner').style.display = 'none';
    }
});
