# Start here

1. Read [PARTICIPANT_CONTRACT.md](../PARTICIPANT_CONTRACT.md) for the platform contract.
2. Read [README.md](README.md) for ACE/SWEPAM collection, strict publication cutoff,
   and the Naive forecast workflow.
3. Run the offline tests from this directory:
   ```bash
   python3 -m unittest discover -s local_tests -p 'test_*.py' -v
   ```
4. With Docker available, run the complete container and mock upload:
   ```bash
   cd local_tests
   bash test_local.sh
   ```
   This downloads live data. A historical timestamp may fail the strict publication
   check; the test defaults to the current UTC hour.
5. Set the team/contact fields in `submission.json` before real submission.
