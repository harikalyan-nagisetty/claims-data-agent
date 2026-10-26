## 1. Purpose
This agent provides the status of a claim, if its Pending,Approved or Denied. it can the change the status from Approved to Denied or from Denied to Approved,pending to Approved or pending to Denied.
They cannot change the status from Denied to pending or Approved to Pending
Sometimes the users might have approved claim as Approved and they want to change the status of claim to Denied 
Sometimes the users might have approved claim as Denied and they want to change the status of claim to Approved 

## 2. Users and example questions
Business users ,Data science and Data engineering teams 
what is the status of the claim 'TP202602182345'?
Can you change the status of claim to Approved
can you change the status of clain to Denied

## 3. Data scope
only claims status table

## 4. Non-goals

Should not move any status from Approved to Pending or Denied to Pending, it should not imagine anything

## 5. Must never (these become tests)

Should not move any status from Approved to Pending or Denied to Pending, it should not imagine anything

## 6. Open questions

When you are unsure mention you dont know


