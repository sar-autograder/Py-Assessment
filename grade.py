import os
import sys
import logging
import time
import json
from optparse import OptionParser

from symbolic.loader import *
from symbolic.explore import ExplorationEngine
from symbolic.grader import GradingEngine
from symbolic.z3_utils.z3_similarity import similarity
from tracing import traceApp

def pretty_print(d: dict) -> None:
	print("{")
	for key, value in d.items():
		print('    ' + str(key) + ' : ' + str(value) + ',')
	print("}")
	print()

def tracefunc(frame, event, arg, indent=[0]):
	if event == "call":
		indent[0] += 2
		print("-" * indent[0] + "> call function", frame.f_code.co_name)
	elif event == "return":
		print("<" + "-" * indent[0], "exit function", frame.f_code.co_name)
		indent[0] -= 2
	return tracefunc

# uncomment to trace function call
# sys.setprofile(tracefunc)

sys.path = [os.path.abspath(os.path.join(os.path.dirname(__file__)))] + sys.path

usage = "usage: %prog [options] <path to reference *.py file> <path to submission *.py file>"
parser = OptionParser(usage=usage)

parser.add_option("-l", "--log", dest="logfile", action="store", help="Save log output to a file", default="")
parser.add_option("-m", "--max-iters", dest="max_iters", type="int", help="Run specified number of iterations (0 for unlimited). Should be used for looping or recursive programs.", default=0)
parser.add_option("-t", "--max-time", dest="max_time", type="float", help="Maximum time for exploration (0 for unlimited). Expect maximum execution time to be around three times the amount.", default=0)
parser.add_option("-q", "--quiet", dest="print_path", action="store_false", help="Quiet mode. Does not print path constraints. Should be activated for looping or recursive programs as printing z3 expressions can be time consuming.", default=True)

(options, args) = parser.parse_args()

if not (options.logfile == ""):
	logging.basicConfig(filename=options.logfile, level=logging.DEBUG, filemode='w', format='%(asctime)s %(levelname)8s %(name)20s: %(message)s')

if len(args) == 0 or not os.path.exists(args[0]):
	parser.error("Missing app to execute")
	sys.exit(1)

filename = os.path.abspath(args[0])
filenameStudent = os.path.abspath(args[1])
	
# Get the object describing the application
app = loaderFactory(filename)
appStudent = loaderFactory(filenameStudent)
if app == None or appStudent == None:
	sys.exit(1)

print ("Reference: " + app.getFile() + "." + app.getEntry())
print ("Grading: " + appStudent.getFile() + "." + appStudent.getEntry())

result = None
try:
	start_time = time.time()

	logging.debug('Exploring reference application')
	explorationEngine = ExplorationEngine(app.createInvocation(), "z3")
	generatedInputs, returnVals, path = explorationEngine.explore(options.max_iters, options.max_time, start_time)
	with open('logs/reference.dot', 'w') as outfile:
		print(explorationEngine.path.toDot(), file=outfile)

	logging.debug('Exploring student application')
	explorationEngineStudent = ExplorationEngine(appStudent.createInvocation(), "z3")
	generatedInputsStudent, returnValsStudent, pathStudent = explorationEngineStudent.explore(options.max_iters, options.max_time, start_time)
	with open('logs/student.dot', 'w') as outfile:
		print(explorationEngineStudent.path.toDot(), file=outfile)

	generatedInputs += generatedInputsStudent
	returnVals += returnValsStudent
	logging.debug('Grading')
	gradingEngine = GradingEngine(app.createInvocation(), appStudent.createInvocation(), "z3")
	tested_case, wrong_case = gradingEngine.grade(generatedInputs, returnVals)
	
	# Replace all PathDeviation or PathEquivalence with Exploration if the case is actually generated by exploration
	for generated_input in generatedInputs:
		generated_input_tuple = tuple(sorted(generated_input))
		for key in tested_case:
			if key == generated_input_tuple:
				tested_case[key][4] = 'Exploration'
		for key in wrong_case:
			if key == generated_input_tuple:
				wrong_case[key][4] = 'Exploration'
	
	# Filter out the cases that are generated by exploration to show path deviation or path equivalence cases
	tested_case_from_formula = tested_case.copy()
	wrong_case_from_formula = wrong_case.copy()
	for generated_input in generatedInputs:
		generated_input_tuple = tuple(sorted(generated_input))
		if generated_input_tuple in tested_case_from_formula:
			del tested_case_from_formula[generated_input_tuple]
		if generated_input_tuple in wrong_case_from_formula:
			del wrong_case_from_formula[generated_input_tuple]

	# Path constraint grading
	pathConstraints = {}
	studentScore = 0
	totalScore = 0
	for case in tested_case:
		referenceOutput = tested_case[case][0]
		studentOutput = tested_case[case][1]
		referencePathConstraint = tested_case[case][2]
		studentPathConstraint = tested_case[case][3]

		# calculate similarity of path constraints
		score = 0
		if referenceOutput == studentOutput:
			# if the output is the same, then the path constraint is correct
			score = 1
		else:
			# else, we need to calculate the similarity of the path constraint that lead to the wrong output with the path constraint that lead to the right output
			similarityScore = similarity(referencePathConstraint, studentPathConstraint)
			if similarityScore == 1:
				# if the path constraints are exactly the same and the output is different, then the mistake is in the output (not the conditions)
				score = 0.2 # need to ask about this, how the scoring works in teaching env
			else:
				score = similarityScore
		
		# two path constraints may produce different results based on the input, so previously correct path constraints may be wrong now
		if (referencePathConstraint, studentPathConstraint) not in pathConstraints:
			pathConstraints[(referencePathConstraint, studentPathConstraint)] = score
		else:
			pathConstraints[(referencePathConstraint, studentPathConstraint)] = min(score, pathConstraints[(referencePathConstraint, studentPathConstraint)])

	# calculate score
	for key in pathConstraints:
		studentScore += pathConstraints[key]
		totalScore += 1
	
	# Don't print path deviation and equivalence if print_path is false
	if not options.print_path:
		for key in tested_case:
			tested_case[key][2] = '-'
			tested_case[key][3] = '-'
		for key in wrong_case:
			wrong_case[key][2] = '-'
			wrong_case[key][3] = '-'
	
	# Print out the results
	print('======')
	print('RESULT')
	print('======')
	print('tested:')
	pretty_print(tested_case)
	print('tested from path dev or path eq:')
	pretty_print(tested_case_from_formula)
	print('wrong:')
	pretty_print(wrong_case)
	print('wrong from path dev or path eq:')
	pretty_print(wrong_case_from_formula)
	print('grade:')
	final_grade = (len(tested_case) - len(wrong_case)) / len(tested_case) * 100
	print(str(final_grade) + '% (' + str(len(tested_case) - len(wrong_case)) + '/' + str(len(tested_case)) + ')')
	print()

	# Print out the path constraints and their respective scores
	if options.print_path:
		print('path constraints:')
		pretty_print(pathConstraints)

	print('path constraint grade:')
	pathConstraintGrade = studentScore / totalScore * 100
	print(str(pathConstraintGrade) + '% (' + str(studentScore) + '/' + str(totalScore) + ')')
	print()

	# Get feedback
	visitedLines = traceApp(appStudent, wrong_case)
	visitedLines = [str(x) for x in visitedLines]
	feedback = 'Please check line(s) ' + ', '.join(visitedLines) + ' in your program.'
	print('feedback:')
	print(feedback)
	print()

	# Save the results to a json file
	tested_case = {str(k):(v[0], v[1]) for k, v in tested_case.items()}	# only get the reference output and the student output
	wrong_case = {str(k):(v[0], v[1]) for k, v in wrong_case.items()}
	resultJson = { 'reference': app.getFile(), 'grading': appStudent.getFile(), 'grade': final_grade, 'path_constraint_grade': pathConstraintGrade, 'tested_case': tested_case, 'wrong_case': wrong_case, 'feedback': feedback }
	with open('res/'+app.getFile()+'-'+appStudent.getFile()+'.json', 'w+') as fp:
		json.dump(resultJson, fp, indent=4)
	
	# Check the result
	result = app.executionComplete(returnVals)

except ImportError as e:
	# createInvocation can raise this
	logging.error(e)
	sys.exit(1)

if result == None or result == True:
	sys.exit(0)
else:
	sys.exit(1)
