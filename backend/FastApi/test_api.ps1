$baseUrl = "http://localhost:8002"

# 1. Test Brand Creation (New Brand)
Write-Host "`n=== Test 1: Create New Brand 'CyberDyne' ==="
$body = @{
    brand_name = "CyberDyne"
    brand_description = "Future tech"
    brand_values = @("AI", "Robotics")
    target_audience = @("Humans")
    instagram_expectations = @("Viral")
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "$baseUrl/workflow/brand-dna" -Method Post -Body $body -ContentType "application/json"
    $sessionId = $response.session_id
    Write-Host "Success! Session ID: $sessionId"
    Write-Host "Message: $($response.message)"
} catch {
    Write-Host "Error: $_"
    exit
}

# 2. Test Brand Update (Same Name, Changed Description)
Write-Host "`n=== Test 2: Update Brand 'CyberDyne' (Should not create duplicate) ==="
$body = @{
    session_id = $sessionId
    brand_name = "CyberDyne"
    brand_description = "Updated Description: Advanced AI Systems"
    brand_values = @("AI", "Robotics", "Security")
    target_audience = @("Enterprises")
    instagram_expectations = "Professional"
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "$baseUrl/workflow/brand-dna" -Method Post -Body $body -ContentType "application/json"
    Write-Host "Success! Message: $($response.message)"
} catch {
    Write-Host "Error: $_"
}

# 3. Test Channel Selection (Verifies Campaign Folder creation)
Write-Host "`n=== Test 3: Select Channel (Instagram) ==="
$body = @{
    session_id = $sessionId
    channel = "instagram"
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "$baseUrl/workflow/channel" -Method Post -Body $body -ContentType "application/json"
    Write-Host "Success! Channel: $($response.channel)"
    Write-Host "Next Step: $($response.next_step)"
} catch {
    Write-Host "Error: $_"
}

# 4. Get Status Check
Write-Host "`n=== Test 4: Check Workflow Status ==="
try {
    $response = Invoke-RestMethod -Uri "$baseUrl/workflow/status/$sessionId" -Method Get
    Write-Host "Current Step: $($response.current_step)"
    Write-Host "Campaign Folder: $($response.progress.campaign_folder)"
    Write-Host "Has Brand DNA: $($response.progress.has_brand_dna)"
} catch {
    Write-Host "Error: $_"
}
