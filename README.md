Vibe coding Serial Terminal Tool <- currently
Add Tab USB for custom communication protocol from Terminal to Master and vice versa <- future
Next step at current stage:
- Check the USB frame/package is correct the requirement.
- Check the fault frame/package indicator
- Find solution to stream data smoothly
- Consider dynamic frame length with CAN ID to identify each data slave node
- Consider simplify mechanism request data from slave each 10ms.
- Consider apply monitoring all slave Rx TimeOut (should split monitoring TimeOut code to function)
