Current Ieation:

With the improvements in ai agents the abstraction has also moved quickly, the earlier agents which were carefully guided using prompts have evolved to not only design but maintain fairly complex systems. As of now, i.e October 2026, Frontier labs have run their own autoimprovement loops wwhere current models are used to improve their future ones, like a flywheel. We are also seeing emergenge of self evolving harness and smaller local models like qwen 2.8 27B or Muse Gilmer are also capable of doing work and match the frontier model of a year or two ago. Google's opensource project Harness2 has shown that its Propose->Probe->Compose cycle can produce a harness that improves the abilities of larger models but the harness delveloped by the frontier models helped improve the abilities of the smaller models. Thus developing a workflow using a larger model and then adapting it for smaller models could have compounding effects for smaller orgs, like their own flywheel.

There are various ways a smaller model could be improved to fit a task, My vision is to tackle two of them for now: 
1.) Harness: It will be developed using the bigger model, and then testing it out with the smaller model we could further refine it.
2.) Finetunning (Data collection): After we have our initial harness optimised for the smaller model, we could then produce data for finetunning usingboth the bigger and smaller model, optimizing the smaller model for specific task
Thus, the more the workflow/ harness will be usesd, the more information we have to improve our local modal for improvement, a mini flywheel of our own.

How it would work?
