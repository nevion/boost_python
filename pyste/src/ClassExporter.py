import exporters
from Exporter import Exporter
from declarations import *
from settings import *
from policies import *
from SingleCodeUnit import SingleCodeUnit
from EnumExporter import EnumExporter
from utils import makeid, enumerate
import copy
import exporterutils
import re

#==============================================================================
# ClassExporter
#==============================================================================
class ClassExporter(Exporter):
    'Generates boost.python code to export a class declaration'
 
    def __init__(self, info, parser_tail=None):
        Exporter.__init__(self, info, parser_tail)
        # sections of code
        self.sections = {}
        # template: each item in the list is an item into the class_<...> 
        # section.
        self.sections['template'] = []  
        # constructor: each item in the list is a parameter to the class_ 
        # constructor, like class_<C>(...)
        self.sections['constructor'] = []
        # inside: everything within the class_<> statement        
        self.sections['inside'] = []        
        # scope: items outside the class statement but within its scope.
        # scope* s = new scope(class<>());
        # ...
        # delete s;
        self.sections['scope'] = []
        # declarations: outside the BOOST_PYTHON_MODULE macro
        self.sections['declaration'] = []
        self.sections['declaration-outside'] = []
        self.sections['include'] = []
        # a list of Constructor instances
        self.constructors = []
        self.wrapper_generator = None
        # a list of code units, generated by nested declarations
        self.nested_codeunits = []
        self._exported_opaque_pointers = {}


    def ScopeName(self):
        return makeid(self.class_._FullName()) + '_scope'


    def Unit(self):
        return makeid(self.class_._name)


    def Name(self):
        return self.class_._FullName()


    def SetDeclarations(self, declarations):
        Exporter.SetDeclarations(self, declarations)
        decl = self.GetDeclaration(self.info.name)
        if isinstance(decl, Typedef):
            self.class_ = self.GetDeclaration(decl._type._name)
            if not self.info.rename:
                self.info.rename = decl._name
        else:
            self.class_ = decl
        self.class_ = copy.deepcopy(self.class_)
        
        
        
    def ClassBases(self):
        all_bases = []       
        for level in self.class_._hierarchy:
            for base in level:
                all_bases.append(base)
        return [self.GetDeclaration(x._name) for x in all_bases] 

    
    def Order(self):
        '''Return the TOTAL number of bases that this class has, including the
        bases' bases.  Do this because base classes must be instantialized
        before the derived classes in the module definition.  
        '''
        return '%s_%s' % (len(self.ClassBases()), self.class_._FullName())
        
    
    def Export(self, codeunit, exported_names):
        self.InheritMethods(exported_names)
        self.MakeNonVirtual()
        if not self.info.exclude:
            self.ExportBasics()
            self.ExportBases(exported_names)
            self.ExportConstructors()
            self.ExportVariables()
            self.ExportVirtualMethods()
            self.ExportMethods()
            self.ExportOperators()
            self.ExportNestedClasses(exported_names)
            self.ExportNestedEnums()
            self.ExportSmartPointer()
            self.ExportOpaquePointerPolicies()
            self.Write(codeunit)


    def InheritMethods(self, exported_names):
        '''Go up in the class hierarchy looking for classes that were not
        exported yet, and then add their public members to this classes
        members, as if they were members of this class. This allows the user to
        just export one type and automatically get all the members from the
        base classes.
        '''
        valid_members = (Method, ClassVariable, NestedClass, ClassOperator,
            ConverterOperator, ClassEnumeration)
        for level in self.class_._hierarchy:
            level_exported = False
            for base in level:
                base = self.GetDeclaration(base._name)
                if base._FullName() not in exported_names:
                    for member in base:
                        if type(member) in valid_members:
                            member = copy.deepcopy(member)   
                            #if type(member) not in (ClassVariable,:
                            #    member.class_ = self.class_._FullName()
                            self.class_._AddMember(member)        
                else:
                    level_exported = True
            if level_exported:
                break
        def IsValid(member):
            return isinstance(member, valid_members) and member._visibility == Scope.public
        self.public_members = [x for x in self.class_ if IsValid(x)]


    def Write(self, codeunit):
        indent = self.INDENT
        boost_ns = namespaces.python
        pyste_ns = namespaces.pyste
        code = ''
        # begin a scope for this class if needed
        nested_codeunits = self.nested_codeunits
        needs_scope = self.sections['scope'] or nested_codeunits
        if needs_scope:
            scope_name = self.ScopeName()
            code += indent + boost_ns + 'scope* %s = new %sscope(\n' %\
                (scope_name, boost_ns)
        # export the template section
        template_params = ', '.join(self.sections['template'])
        code += indent + boost_ns + 'class_< %s >' % template_params
        # export the constructor section
        constructor_params = ', '.join(self.sections['constructor'])
        code += '(%s)\n' % constructor_params
        # export the inside section
        in_indent = indent*2
        for line in self.sections['inside']:
            code += in_indent + line + '\n' 
        # write the scope section and end it
        if not needs_scope:
            code += indent + ';\n'
        else:
            code += indent + ');\n'
            for line in self.sections['scope']:
                code += indent + line + '\n'
            # write the contents of the nested classes
            for nested_unit in nested_codeunits:
                code += '\n' + nested_unit.Section('module')
            # close the scope
            code += indent + 'delete %s;\n' % scope_name
            
        # write the code to the module section in the codeunit        
        codeunit.Write('module', code + '\n')
        
        # write the declarations to the codeunit        
        declarations = '\n'.join(self.sections['declaration'])
        for nested_unit in nested_codeunits:
            declarations += nested_unit.Section('declaration')
        if declarations:
            codeunit.Write('declaration', declarations + '\n')
        declarations_outside = '\n'.join(self.sections['declaration-outside'])
        if declarations_outside:
            codeunit.Write('declaration-outside', declarations_outside + '\n')

        # write the includes to the codeunit
        includes = '\n'.join(self.sections['include'])
        for nested_unit in nested_codeunits:
            includes += nested_unit.Section('include')
        if includes:
            codeunit.Write('include', includes)


    def Add(self, section, item):
        'Add the item into the corresponding section'
        self.sections[section].append(item)

        
    def ExportBasics(self):
        'Export the name of the class and its class_ statement'
        self.Add('template', self.class_._FullName())
        name = self.info.rename or self.class_._name
        self.Add('constructor', '"%s"' % name)
        
        
    def ExportBases(self, exported_names):
        'Expose the bases of the class into the template section'        
        hierarchy = self.class_._hierarchy
        for level in hierarchy:
            exported = []
            for base in level:
                if base._visibility == Scope.public and base._name in exported_names:
                    exported.append(base._name)
            if exported:
                code = namespaces.python + 'bases< %s > ' %  (', '.join(exported))
                self.Add('template', code)         


    def ExportConstructors(self):
        '''Exports all the public contructors of the class, plus indicates if the 
        class is noncopyable.
        '''
        py_ns = namespaces.python
        indent = self.INDENT
        
        def init_code(cons):
            'return the init<>() code for the given contructor'
            param_list = [p._FullName() for p in cons._parameters]
            min_params_list = param_list[:cons._minArgs]
            max_params_list = param_list[cons._minArgs:]
            min_params = ', '.join(min_params_list)
            max_params = ', '.join(max_params_list)
            init = py_ns + 'init< '
            init += min_params
            if max_params:
                if min_params:
                    init += ', '
                init += py_ns + ('optional< %s >' % max_params)
            init += ' >()'    
            return init
        
        constructors = [x for x in self.public_members if isinstance(x, Constructor)]
        self.constructors = constructors[:]
        # don't export the copy constructor if the class is abstract
        if self.class_._abstract:
            for cons in constructors:
                if cons._IsCopy():
                    constructors.remove(cons)
                    break
        if not constructors:
            # declare no_init
            self.Add('constructor', py_ns + 'no_init') 
        else:
            # write the constructor with less parameters to the constructor section
            smaller = None
            for cons in constructors:
                if smaller is None or len(cons._parameters) < len(smaller._parameters):
                    smaller = cons
            assert smaller is not None
            self.Add('constructor', init_code(smaller))
            constructors.remove(smaller)
            # write the rest to the inside section, using def()
            for cons in constructors:
                code = '.def(%s)' % init_code(cons) 
                self.Add('inside', code)
        # check if the class is copyable
        if not self.class_._HasCopyConstructor() or self.class_._abstract:
            self.Add('template', namespaces.boost + 'noncopyable')
            
        
    def ExportVariables(self):
        'Export the variables of the class, both static and simple variables'
        vars = [x for x in self.public_members if isinstance(x, Variable)]
        for var in vars:
            if self.info[var._name].exclude: 
                continue
            name = self.info[var._name].rename or var._name
            fullname = var._FullName() 
            if var._type._const:
                def_ = '.def_readonly'
            else:
                def_ = '.def_readwrite'
            code = '%s("%s", &%s)' % (def_, name, fullname)
            self.Add('inside', code)

    
    def OverloadName(self, method):
        'Returns the name of the overloads struct for the given method'
        name = makeid(method._FullName())
        overloads = '_overloads_%i_%i' % (method._minArgs, method._maxArgs)    
        return name + overloads

    
    def GetAddedMethods(self):
        added_methods = self.info.__added__
        result = []
        if added_methods:
            for name, rename in added_methods:
                decl = self.GetDeclaration(name)
                self.info[name].rename = rename
                result.append(decl)
        return result

                
    def ExportMethods(self):
        '''Export all the non-virtual methods of this class, plus any function
        that is to be exported as a method'''
            
        declared = {}
        def DeclareOverloads(m):
            'Declares the macro for the generation of the overloads'
            if (isinstance(m, Method) and m._static) or type(m) == Function:
                func = m._FullName()
                macro = 'BOOST_PYTHON_FUNCTION_OVERLOADS'
            else:
                func = m._name
                macro = 'BOOST_PYTHON_MEMBER_FUNCTION_OVERLOADS' 
            code = '%s(%s, %s, %i, %i)\n' % (macro, self.OverloadName(m), func, m._minArgs, m._maxArgs)
            if code not in declared:
                declared[code] = True
                self.Add('declaration', code)


        def Pointer(m):
            'returns the correct pointer declaration for the method m'
            # check if this method has a wrapper set for him
            wrapper = self.info[m._name].wrapper
            if wrapper:
                return '&' + wrapper.FullName()
            else:
                return m._PointerDeclaration() 

        def IsExportable(m):
            'Returns true if the given method is exportable by this routine'
            ignore = (Constructor, ClassOperator, Destructor)
            return isinstance(m, Function) and not isinstance(m, ignore) and not m._virtual        
        
        methods = [x for x in self.public_members if IsExportable(x)]        
        methods.extend(self.GetAddedMethods())
        
        for method in methods:
            method_info = self.info[method._name]
            
            # skip this method if it was excluded by the user
            if method_info.exclude:
                continue 

            # rename the method if the user requested
            name = method_info.rename or method._name
            
            # warn the user if this method needs a policy and doesn't have one
            method_info.policy = exporterutils.HandlePolicy(method, method_info.policy)
            
            # check for policies
            policy = method_info.policy or ''
            if policy:
                policy = ', %s%s()' % (namespaces.python, policy.Code())
            # check for overloads
            overload = ''
            if method._minArgs != method._maxArgs:
                # add the overloads for this method
                DeclareOverloads(method)
                overload_name = self.OverloadName(method)
                overload = ', %s%s()' % (namespaces.pyste, overload_name)
        
            # build the .def string to export the method
            pointer = Pointer(method)
            code = '.def("%s", %s' % (name, pointer)
            code += policy
            code += overload
            code += ')'
            self.Add('inside', code)
            # static method
            if isinstance(method, Method) and method._static:
                code = '.staticmethod("%s")' % name
                self.Add('inside', code)
            # add wrapper code if this method has one
            wrapper = method_info.wrapper
            if wrapper and wrapper.code:
                self.Add('declaration', wrapper.code)


    def MakeNonVirtual(self):
        '''Make all methods that the user indicated to no_override no more virtual, delegating their
        export to the ExportMethods routine'''
        for member in self.class_:
            if type(member) == Method and member._virtual:
                member._virtual = not self.info[member._name].no_override 


    def ExportVirtualMethods(self):        
        # check if this class has any virtual methods
        has_virtual_methods = False
        for member in self.class_:
            if type(member) == Method and member._virtual:
                has_virtual_methods = True
                break

        if has_virtual_methods:
            generator = _VirtualWrapperGenerator(self.class_, self.ClassBases(), self.info)
            self.Add('template', generator.FullName())
            for definition in generator.GenerateDefinitions():
                self.Add('inside', definition)
            self.Add('declaration', generator.GenerateVirtualWrapper(self.INDENT))
            

    # operators natively supported by boost
    BOOST_SUPPORTED_OPERATORS = '+ - * / % ^ & ! ~ | < > == != <= >= << >> && || += -='\
        '*= /= %= ^= &= |= <<= >>='.split()
    # create a map for faster lookup
    BOOST_SUPPORTED_OPERATORS = dict(zip(BOOST_SUPPORTED_OPERATORS, range(len(BOOST_SUPPORTED_OPERATORS))))

    # a dict of operators that are not directly supported by boost, but can be exposed
    # simply as a function with a special name
    BOOST_RENAME_OPERATORS = {
        '()' : '__call__',
    }

    # converters which have a special name in python
    # it's a map of a regular expression of the converter's result to the
    # appropriate python name
    SPECIAL_CONVERTERS = {
        re.compile(r'(const)?\s*double$') : '__float__',
        re.compile(r'(const)?\s*float$') : '__float__',
        re.compile(r'(const)?\s*int$') : '__int__',
        re.compile(r'(const)?\s*long$') : '__long__',
        re.compile(r'(const)?\s*char\s*\*?$') : '__str__',
        re.compile(r'(const)?.*::basic_string<.*>\s*(\*|\&)?$') : '__str__',
    }
        
    
    def ExportOperators(self):
        'Export all member operators and free operators related to this class'
        
        def GetFreeOperators():
            'Get all the free (global) operators related to this class'
            operators = []
            for decl in self.declarations:
                if isinstance(decl, Operator):
                    # check if one of the params is this class
                    for param in decl._parameters:
                        if param._name == self.class_._FullName():
                            operators.append(decl)
                            break
            return operators

        def GetOperand(param):
            'Returns the operand of this parameter (either "self", or "other<type>")'
            if param._name == self.class_._FullName():
                return namespaces.python + 'self'
            else:
                return namespaces.python + ('other< %s >()' % param._name)


        def HandleSpecialOperator(operator):
            # gatter information about the operator and its parameters
            result_name = operator._result._name                        
            param1_name = ''
            if operator._parameters:
                param1_name = operator._parameters[0]._name
                
            # check for str
            ostream = 'basic_ostream'
            is_str = result_name.find(ostream) != -1 and param1_name.find(ostream) != -1
            if is_str:
                namespace = namespaces.python + 'self_ns::'
                self_ = namespaces.python + 'self'
                return '.def(%sstr(%s))' % (namespace, self_)

            # is not a special operator
            return None
                

        
        frees = GetFreeOperators()
        members = [x for x in self.public_members if type(x) == ClassOperator]
        all_operators = frees + members
        operators = [x for x in all_operators if not self.info['operator'][x._name].exclude]
        
        for operator in operators:
            # gatter information about the operator, for use later
            wrapper = self.info['operator'][operator._name].wrapper
            if wrapper:
                pointer = '&' + wrapper.FullName()
                if wrapper.code:
                    self.Add('declaration', wrapper.code)
            else:
                pointer = operator._PointerDeclaration()                 
            rename = self.info['operator'][operator._name].rename

            # check if this operator will be exported as a method
            export_as_method = wrapper or rename or operator._name in self.BOOST_RENAME_OPERATORS
            
            # check if this operator has a special representation in boost
            special_code = HandleSpecialOperator(operator)
            has_special_representation = special_code is not None
            
            if export_as_method:
                # export this operator as a normal method, renaming or using the given wrapper
                if not rename:
                    if wrapper:
                        rename = wrapper.name
                    else:
                        rename = self.BOOST_RENAME_OPERATORS[operator._name]
                policy = ''
                policy_obj = self.info['operator'][operator._name].policy
                if policy_obj:
                    policy = ', %s()' % policy_obj.Code() 
                self.Add('inside', '.def("%s", %s%s)' % (rename, pointer, policy))
            
            elif has_special_representation:
                self.Add('inside', special_code)
                
            elif operator._name in self.BOOST_SUPPORTED_OPERATORS:
                # export this operator using boost's facilities
                op = operator
                is_unary = isinstance(op, Operator) and len(op._parameters) == 1 or\
                           isinstance(op, ClassOperator) and len(op._parameters) == 0
                if is_unary:
                    self.Add('inside', '.def( %s%sself )' % \
                        (operator._name, namespaces.python))
                else:
                    # binary operator
                    if len(operator._parameters) == 2:
                        left_operand = GetOperand(operator._parameters[0])
                        right_operand = GetOperand(operator._parameters[1])
                    else:
                        left_operand = namespaces.python + 'self'
                        right_operand = GetOperand(operator._parameters[0])
                    self.Add('inside', '.def( %s %s %s )' % \
                        (left_operand, operator._name, right_operand))

        # export the converters.
        # export them as simple functions with a pre-determined name

        converters = [x for x in self.public_members if type(x) == ConverterOperator]
                
        def ConverterMethodName(converter):
            result_fullname = converter._result._FullName()
            result_name = converter._result._name
            for regex, method_name in self.SPECIAL_CONVERTERS.items():
                if regex.match(result_fullname):
                    return method_name
            else:
                # extract the last name from the full name
                result_name = makeid(result_name)
                return 'to_' + result_name
            
        for converter in converters:
            info = self.info['operator'][converter._result._FullName()]
            # check if this operator should be excluded
            if info.exclude:
                continue
            
            special_code = HandleSpecialOperator(converter)
            if info.rename or not special_code:
                # export as method
                name = info.rename or ConverterMethodName(converter)
                pointer = converter._PointerDeclaration()
                policy_code = ''
                if info.policy:
                    policy_code = ', %s()' % info.policy.Code()
                self.Add('inside', '.def("%s", %s%s)' % (name, pointer, policy_code))
                    
            elif special_code:
                self.Add('inside', special_code)



    def ExportNestedClasses(self, exported_names):
        nested_classes = [x for x in self.public_members if isinstance(x, NestedClass)]
        for nested_class in nested_classes:
            nested_info = self.info[nested_class._name]
            nested_info.include = self.info.include
            nested_info.name = nested_class._FullName()
            exporter = ClassExporter(nested_info)
            exporter.SetDeclarations(self.declarations)
            codeunit = SingleCodeUnit(None, None)
            exporter.Export(codeunit, exported_names)
            self.nested_codeunits.append(codeunit)


    def ExportNestedEnums(self):
        nested_enums = [x for x in self.public_members if isinstance(x, ClassEnumeration)]
        for enum in nested_enums:
            enum_info = self.info[enum._name]
            enum_info.include = self.info.include
            enum_info.name = enum._FullName()
            exporter = EnumExporter(enum_info)
            exporter.SetDeclarations(self.declarations)
            codeunit = SingleCodeUnit(None, None)
            exporter.Export(codeunit, None)
            self.nested_codeunits.append(codeunit)


    def ExportSmartPointer(self):
        smart_ptr = self.info.smart_ptr
        if smart_ptr:
            class_name = self.class_._FullName()
            smart_ptr = smart_ptr % class_name
            self.Add('scope', '%s::register_ptr_to_python< %s >();' % (namespaces.python, smart_ptr))
            

    def ExportOpaquePointerPolicies(self):
        # check all methods for 'return_opaque_pointer' policies
        methods = [x for x in self.public_members if isinstance(x, Method)]
        for method in methods:
            return_opaque_policy = return_value_policy(return_opaque_pointer)
            if self.info[method._name].policy == return_opaque_policy:
                macro = 'BOOST_PYTHON_OPAQUE_SPECIALIZED_TYPE_ID(%s)' % method._result._name
                if macro not in self._exported_opaque_pointers:
                    self.Add('declaration-outside', macro)
                    self._exported_opaque_pointers[macro] = 1


#==============================================================================
# Virtual Wrapper utils
#==============================================================================

def _ParamsInfo(m, count=None):
    if count is None:
        count = len(m._parameters)
    param_names = ['p%i' % i for i in range(count)]
    param_types = [x._FullName() for x in m._parameters[:count]]
    params = ['%s %s' % (t, n) for t, n in zip(param_types, param_names)]
    #for i, p in enumerate(m.parameters[:count]):
    #    if p.default is not None:
    #        #params[i] += '=%s' % p.default
    #        params[i] += '=%s' % (p.name + '()')
    params = ', '.join(params) 
    return params, param_names, param_types

            
class _VirtualWrapperGenerator(object):
    'Generates code to export the virtual methods of the given class'

    def __init__(self, class_, bases, info):
        self.class_ = class_
        self.bases = bases[:]
        self.info = info
        self.wrapper_name = makeid(class_._FullName()) + '_Wrapper'
        self.virtual_methods = None
        self._method_count = {}
        self.GenerateVirtualMethods()


    def DefaultImplementationNames(self, method):
        '''Returns a list of default implementations for this method, one for each
        number of default arguments. Always returns at least one name, and return from 
        the one with most arguments to the one with the least.
        '''
        base_name = 'default_' + method._name 
        minArgs = method._minArgs
        maxArgs = method._maxArgs
        if minArgs == maxArgs:
            return [base_name]
        else:
            return [base_name + ('_%i' % i) for i in range(minArgs, maxArgs+1)]                


    def Declaration(self, method, indent):
        '''Returns a string with the declarations of the virtual wrapper and
        its default implementations. This string must be put inside the Wrapper
        body.        
        '''
        pyste = namespaces.pyste
        python = namespaces.python
        rename = self.info[method._name].rename or method._name
        result = method._result._FullName()
        return_str = 'return '
        if result == 'void':
            return_str = ''
        params, param_names, param_types = _ParamsInfo(method)
        constantness = ''
        if method._const:
            constantness = ' const'
        
        # call_method callback
        decl  = indent + '%s %s(%s)%s {\n' % (result, method._name, params, constantness)
        param_names_str = ', '.join(param_names)
        if param_names_str:
            param_names_str = ', ' + param_names_str
        decl += indent*2 + '%s%scall_method< %s >(self, "%s"%s);\n' %\
            (return_str, python, result, rename, param_names_str)
        decl += indent + '}\n'

        # default implementations (with overloading)
        def DefaultImpl(method, param_names):
            'Return the body of a default implementation wrapper'
            wrapper = self.info[method._name].wrapper
            if not wrapper:
                # return the default implementation of the class
                return '%s%s::%s(%s);\n' % \
                    (return_str, self.class_._FullName(), method._name, ', '.join(param_names)) 
            else:
                # return a call for the wrapper
                params = ', '.join(['this'] + param_names)
                return '%s%s(%s);\n' % (return_str, wrapper.FullName(), params)
                
        if not method._abstract and method._visibility != Scope.private:
            minArgs = method._minArgs
            maxArgs = method._maxArgs
            impl_names = self.DefaultImplementationNames(method)
            for impl_name, argNum in zip(impl_names, range(minArgs, maxArgs+1)): 
                params, param_names, param_types = _ParamsInfo(method, argNum)            
                decl += '\n'
                decl += indent + '%s %s(%s)%s {\n' % (result, impl_name, params, constantness)
                decl += indent*2 + DefaultImpl(method, param_names)
                decl += indent + '}\n'                
        return decl
            

    def MethodDefinition(self, method):
        '''Returns a list of lines, which should be put inside the class_
        statement to export this method.'''
        # dont define abstract methods
        pyste = namespaces.pyste
        rename = self.info[method._name].rename or method._name
        default_names = self.DefaultImplementationNames(method)
        class_name = self.class_._FullName()
        wrapper_name = pyste + self.wrapper_name
        result = method._result._FullName()
        is_method_unique = method._is_unique
        constantness = ''
        if method._const:
            constantness = ' const'
        
        # create a list of default-impl pointers
        minArgs = method._minArgs
        maxArgs = method._maxArgs 
        if is_method_unique:
            default_pointers = ['&%s::%s' % (wrapper_name, x) for x in default_names]
        else:
            default_pointers = []
            for impl_name, argNum in zip(default_names, range(minArgs, maxArgs+1)):
                param_list = [x._FullName() for x in method._parameters[:argNum]]
                params = ', '.join(param_list)             
                signature = '%s (%s::*)(%s)%s' % (result, wrapper_name, params, constantness)
                default_pointer = '(%s)&%s::%s' % (signature, wrapper_name, impl_name)
                default_pointers.append(default_pointer)
            
        # get the pointer of the method
        pointer = method._PointerDeclaration()

        # Add policy to overloaded methods also
        policy = self.info[method._name].policy or ''
        if policy:
            policy = ', %s%s()' % (namespaces.python, policy.Code())

        # generate the defs
        definitions = []
        # basic def
        definitions.append('.def("%s", %s, %s%s)' % (rename, pointer, default_pointers[-1], policy))
        for default_pointer in default_pointers[:-1]:
            definitions.append('.def("%s", %s%s)' % (rename, default_pointer, policy))
        return definitions

    
    def FullName(self):
        return namespaces.pyste + self.wrapper_name


    def GenerateVirtualMethods(self):
        '''To correctly export all virtual methods, we must also make wrappers
        for the virtual methods of the bases of this class, as if the methods
        were from this class itself.
        This method creates the instance variable self.virtual_methods.
        '''        
        def IsVirtual(m):
            return type(m) is Method and \
                m._virtual and \
                m._visibility != Scope.private
                                      
        all_methods = [x for x in self.class_ if IsVirtual(x)]
        for base in self.bases:
            base_methods = [copy.deepcopy(x) for x in base if IsVirtual(x)]
            for base_method in base_methods:
                base_method.class_ = self.class_._FullName()
                all_methods.append(base_method)
        
        # extract the virtual methods, avoiding duplications. The duplication
        # must take in account the full signature without the class name, so 
        # that inherited members are correctly excluded if the subclass overrides
        # them.
        def MethodSig(method):
            if method._const:
                const = 'const'
            else:
                const = ''
            if method._result:
                result = method._result._FullName()
            else:
                result = ''
            params = ', '.join([x._FullName() for x in method._parameters]) 
            return '%s %s(%s) %s' % (result, method._name, params, const) 
        
        self.virtual_methods = []
        already_added = {}
        for member in all_methods:
            sig = MethodSig(member)
            if IsVirtual(member) and not sig in already_added:
                self.virtual_methods.append(member)
                already_added[sig] = 0
            
    
    def Constructors(self):
        return self.class_._Constructors(publics_only=True)
    
        
    def GenerateDefinitions(self):
        defs = []
        for method in self.virtual_methods:
            exclude = self.info[method._name].exclude
            # generate definitions only for public methods and non-abstract methods
            if method._visibility == Scope.public and not method._abstract and not exclude:
                defs.extend(self.MethodDefinition(method))
        return defs

        
    def GenerateVirtualWrapper(self, indent):
        'Return the wrapper for this class'
        
        # generate the class code
        class_name = self.class_._FullName()
        code = 'struct %s: %s\n' % (self.wrapper_name, class_name)
        code += '{\n'
        # generate constructors (with the overloads for each one)
        for cons in self.Constructors(): # only public constructors
            minArgs = cons._minArgs
            maxArgs = cons._maxArgs
            # from the min number of arguments to the max number, generate
            # all version of the given constructor
            cons_code = ''
            for argNum in range(minArgs, maxArgs+1):
                params, param_names, param_types = _ParamsInfo(cons, argNum)
                if params:
                    params = ', ' + params
                cons_code += indent + '%s(PyObject* self_%s):\n' % \
                    (self.wrapper_name, params)
                cons_code += indent*2 + '%s(%s), self(self_) {}\n\n' % \
                    (class_name, ', '.join(param_names))
            code += cons_code
        # generate the body
        body = []
        for method in self.virtual_methods:
            if not self.info[method._name].exclude:
                body.append(self.Declaration(method, indent))            
        body = '\n'.join(body) 
        code += body + '\n'
        # add the self member
        code += indent + 'PyObject* self;\n'
        code += '};\n'
        return code 
