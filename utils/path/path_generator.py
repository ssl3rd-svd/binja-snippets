import logging
from utils.path.node import *
import networkx as nx
from pprint import pprint
from binaryninja import *
from enum import Enum, Flag, auto
import uuid

RED = '\033[1;31;48m'
GREEN = '\033[1;32;40m'
YELLOW = '\033[1;33;40m'
END = '\033[1;37;0m'

class PathType(Enum):
    SINGLE_FUNCTION = 1,
    LINEAR_NODES = 2,
    TREE_NODES = 3

class PathGenOption(Flag):
    DEFAULT = auto()
    POSSIBLE_VALUE_UPDATE = auto()
    CHECK_FEASIBLE = auto()


class PathObject():
    def __init__(self, bv: BinaryView, type: PathType, path: None|list[tuple]|tuple[list[tuple], list[tuple]], head: Function, source: PEdge, sink: PEdge, option: PathGenOption) -> None:
        self.bv = bv
        self.type = type
        self.name = str(uuid.uuid4()) # FIXME: change name?
        self.option = option
        self.path: None|list[tuple]|tuple[list[tuple]] = path # when single|linear|tree
        self.graph = nx.DiGraph() # with PNodes
        self.head_function: Function = head
        self.head: PNode
        self.source: PEdge = source
        self.sink: PEdge = sink

        self.nodes = dict() # { Function: PNode }
        self.edges = dict() # { call_site_address int : PEdge }

        self.highlight_addr: dict[Function, list[int]] = dict()

        if self.type == PathType.SINGLE_FUNCTION:
            self.generate_single_node()
        elif self.type == PathType.LINEAR_NODES:
            self.generate_linear_nodes(path=self.path)
        elif self.type == PathType.TREE_NODES: 
            self.generate_tree_nodes(self.path[0], self.path[1])
        else:
            logging.error(f'Please use this class with right type')
            raise NotImplemented
        

    def is_single_function(self):
        if self.path is None:
            if self.source.start.start == self.sink.start.start:
                return True
        return False

    def is_linear(self):
        if self.head_function.start == self.source.start.start:
            return True
        return False

    def generate_single_node(self):
        logging.debug(f'source node and sink are same at {self.source.start}')

        node = PNode(self.source.start)
        self.nodes[self.source.start] = node
        self.head = node

        # fill tainted variable to object by backward tainting
        self.backward_tainting(type='sink')
        self.backward_tainting(type='source')

        self.graph.add_node(node)

        logging.debug(f'Creating a path graph is done')
        

    def generate_linear_nodes(self, path: list[tuple]):
        logging.debug(f'The head node is a source function {self.head_function.name}')

        for start, end, call_site_address in path:
            logging.debug(f'start: {start} -> end: {end} at call_site: 0x{call_site_address:x}')
            start: Function
            end: Function
            call_site_address: int

            edge = PEdge(start=start, end=end, address=call_site_address)

            if PathGenOption.POSSIBLE_VALUE_UPDATE in self.option:
                edge.update_possible_value()
            
            self.edges[call_site_address] = edge

            # node initial set up
            if self.nodes.get(start) is None:
                _node = PNode(start)
                self.nodes[start] = _node
            if self.nodes.get(end) is None:
                _node = PNode(end)
                self.nodes[end] = _node

            start_node: PNode = self.nodes.get(edge.start)
            end_node: PNode = self.nodes.get(edge.end)

            start_node.next = end_node
            start_node.next_at = edge
            end_node.prev = start_node
            end_node.prev_at = edge

        # fill tainted variable to object by backward tainting
        self.backward_tainting(type='sink', path=path)
        self.backward_tainting(type='source')

        # make head node
        head_node = self.nodes.get(self.source.start)
        self.head = head_node

        # make graph
        self.make_graph()


    def make_graph(self):

        for _, edge in self.edges.items():
            start_node: PNode = self.nodes.get(edge.start)
            end_node: PNode = self.nodes.get(edge.end)

            self.graph.add_edge(start_node, end_node, call_site=edge)

        logging.debug(f'Creating a path graph is done')


    def backward_tainting(self, type: str, path: list[tuple]=[]):

        if type == 'sink':
            backward_edges = [self.sink]
        elif type == 'source':
            backward_edges = [self.source]

        for _, _, call_site in path[::-1]:
            backward_edges.append(self.edges.get(call_site))

        tmp = []
        for edge in backward_edges:
            logging.debug(f'backward {edge}')
            if edge.taint_args is None:
                # when this edge is not source or sink.
                edge.taint_args = tmp
            
            ssavar = edge.get_ssavars_to_taint()
            
            if type == 'sink':
                stack_vars, global_vars, heap_vars = self.get_related_vars_in_function_backward(function=edge.start, vars=ssavar)
                self.nodes[edge.start].tainted_vars_from_sink = stack_vars
                self.nodes[edge.start].global_vars.extend(global_vars)
                self.nodes[edge.start].heap_vars.extend(heap_vars)
            elif type == 'source':
                stack_vars, global_vars, heap_vars = self.get_related_vars_in_function_backward(function=edge.start, vars=ssavar)
                stack_vars2, global_vars2, heap_vars2 = self.get_related_vars_in_function_forward(function=edge.start, vars=stack_vars)

                self.nodes[edge.start].tainted_vars_from_source = stack_vars + stack_vars2
                self.nodes[edge.start].global_vars.extend(global_vars + global_vars2)
                self.nodes[edge.start].heap_vars.extend(heap_vars + heap_vars2)

            tmp = [int(arg.var.name.split('arg')[1]) - 1 for arg in stack_vars if arg.var.name.startswith('arg')]

    def generate_tree_nodes(self, source_path:list[tuple], sink_path:list[tuple]):
        # TODO:
        logging.debug(f'Head: {self.head_function.name}, Source: {self.source.start.name}, Sink: {self.sink.start.name}')
        

    def get_related_vars_in_function_forward(self, function: Function, vars: list[SSAVariable]) -> tuple[list[SSAVariable], list[MediumLevelILConstPtr], list[SSAVariable]]:
        '''
        ????????? ?????? ????????? ????????? SSAVariable??? ????????? ?????? ????????? ??????.
        ?????? ????????? (stack, global, heap) ??????

        TODO: 
        1. function call??? ????????? ???, ?????? ???????????? ??? taint??? ??????
           - function dataflow analysis??? ???????????? ????????? taint ?????? ??????
        2. ??????????????? ?????? ??????????????? basic block??? ???????????? ??????
           - function ??? dataflow analysis ??????
        '''
        stack_vars = []
        global_vars = []
        heap_vars = []

        visited = []
        taint = []

        # for highlighting
        self.highlight_addr[function] = list()

        stack_vars.extend(vars)

        for var in vars:
            for use in function.mlil.ssa_form.get_ssa_var_uses(var):
                taint.append( use )

        while len(taint) > 0:
            track_var = taint.pop()

            # TODO: path ?????? ??????????????? ??????
            # bb = bv.get_basic_blocks_at(track_var.address)
            # if not path.has_node(bb):
            #     continue
            
            # for highlighting
            self.highlight_addr[function].append(track_var.address)
            
            # TODO: ???????????? ????????????
            

            if track_var in visited:
                continue

            visited.append(track_var)


            if track_var.operation not in ( MediumLevelILOperation.MLIL_SET_VAR_SSA, MediumLevelILOperation.MLIL_SET_VAR, \
            MediumLevelILOperation.MLIL_VAR_PHI, MediumLevelILOperation.MLIL_STORE_SSA ):
                continue

            if track_var.operation == MediumLevelILOperation.MLIL_STORE_SSA:
                if track_var.dest.operation == MediumLevelILOperation.MLIL_CONST_PTR:
                    # FIXME: global ????????? ????????? ?????? ?????????, global ????????? use ?????? ????????? ????????????
                    global_vars.append(track_var.dest)
                    continue

            elif track_var.operation == MediumLevelILOperation.MLIL_SET_VAR or \
            track_var.operation == MediumLevelILOperation.MLIL_SET_VAR_SSA:
            # SET_VAR??? ?????? 
                if type(track_var.dest) == SSAVariable:
                    stack_vars.append(track_var.dest)
                    uses = track_var.ssa_form.function.get_ssa_var_uses(track_var.dest)
                    for use in uses:
                        taint.append(use)
                    continue

                var = track_var.src.ssa_form
                if var.operation == MediumLevelILOperation.MLIL_ADD or \
                    var.operation == MediumLevelILOperation.MLIL_SUB or \
                    var.operation == MediumLevelILOperation.MLIL_MUL or \
                    var.operation == MediumLevelILOperation.MLIL_DIVS:
                        #src??? operation??? ??????, VAR ??????
                    if var.left.operation == MediumLevelILOperation.MLIL_VAR_SSA:
                        var = var.left
                    else:
                        var = var.right
                while type(var) != binaryninja.mediumlevelil.SSAVariable: # MediumLevelILOperation.MLIL_VAR_ALIASED
                    var = var.src
                
                stack_vars.append(var)
                uses = track_var.ssa_form.function.get_ssa_var_uses(var)

                # TODO: call ?????? ?????? taint ???????????? ??????, a = sub(b) ??????
                # TODO: return??? ????????? taint ???????????? ??????
                for use in uses:
                    taint.append(use)

            # TODO: call ?????? ?????? taint ???????????? ??????, sub(b) ??????
            elif track_var.operation == MediumLevelILOperation.MLIL_CALL_SSA:
                pass
                # TODO: return??? ????????? taint ???????????? ??????

        
        return (stack_vars, global_vars, heap_vars)




    def get_related_vars_in_function_backward(self, function: Function, vars: list[SSAVariable]) -> tuple[list[SSAVariable], list[MediumLevelILConstPtr], list[SSAVariable]]:
        '''
        ????????? ?????? ????????? ????????? SSAVariable??? ????????? ?????? ????????? ??????.
        ?????? ????????? (stack, global, heap) ??????

        TODO: 
        1. function call??? ????????? ???, ?????? ???????????? ??? taint??? ??????
           - function dataflow analysis??? ???????????? ????????? taint ?????? ??????
        2. ??????????????? ?????? ??????????????? basic block??? ???????????? ??????
           - function ??? dataflow analysis ??????
        '''
        stack_vars = []
        global_vars = []
        heap_vars = []

        visited = []
        taint = []
        
        # for highlighting
        self.highlight_addr[function] = list()

        stack_vars.extend(vars)

        # TODO: var use ?????? ??? definition ????????????
        for var in vars:
            taint.append( function.mlil.ssa_form.get_ssa_var_definition(var) )

        while len(taint) > 0:
            track_var = taint.pop()

            
            # TODO: path ?????? ??????????????? ??????
            # bb = bv.get_basic_blocks_at(track_var.address)
            # if not path.has_node(bb):
            #     continue
            
            # for highlighting
            self.highlight_addr[function].append(track_var.address)
            
            # TODO: ???????????? ????????????
            

            if track_var in visited:
                continue

            visited.append(track_var)

            # FIXME: ?????? Operation??? ?????? SSAVariable ???????????? ????????? ??????
            if track_var.operation not in ( MediumLevelILOperation.MLIL_SET_VAR_SSA, MediumLevelILOperation.MLIL_SET_VAR, \
            MediumLevelILOperation.MLIL_VAR_PHI ):
                continue

            if track_var.operation == MediumLevelILOperation.MLIL_SET_VAR or \
            track_var.operation == MediumLevelILOperation.MLIL_SET_VAR_SSA:
            # SET_VAR??? ?????? 
                if track_var.src.operation == MediumLevelILOperation.MLIL_CONST_PTR:
                    #SET_VAR??? src??? CONST_PTR??? ??????
                    continue
                elif track_var.src.operation == MediumLevelILOperation.MLIL_ADDRESS_OF:
                    continue
                elif track_var.src.operation == MediumLevelILOperation.MLIL_LOAD_SSA:
                    # TODO: ???????????? ??????
                    # global variable??? heap variable ??????????????? ???????????? ??????
                    if track_var.src.src.operation == MediumLevelILOperation.MLIL_CONST_PTR:
                        global_vars.append(track_var.src.src)
                    continue
                # src trace
                var = track_var.src.ssa_form

                if var.operation == MediumLevelILOperation.MLIL_LOAD_SSA:
                    #LOAD??? ?????? ?????? src??? ??????
                    var = var.src
                if var.operation == MediumLevelILOperation.MLIL_ADD or \
                var.operation == MediumLevelILOperation.MLIL_SUB or \
                var.operation == MediumLevelILOperation.MLIL_MUL or \
                var.operation == MediumLevelILOperation.MLIL_DIVS:
                    #src??? operation??? ??????, VAR ??????
                    if var.left.operation == MediumLevelILOperation.MLIL_VAR_SSA:
                        var = var.left
                    else:
                        var = var.right
                while type(var) != binaryninja.mediumlevelil.SSAVariable: # MediumLevelILOperation.MLIL_VAR_ALIASED
                    var = var.src
                
                stack_vars.append(var)
                def_ref = track_var.ssa_form.function.get_ssa_var_definition(var)
                if def_ref == None:
                    continue

                taint.append(def_ref)
                
                # TODO: call ?????? ?????? taint ???????????? ??????, a = sub(b) ??????
                # TODO: return??? ????????? taint ???????????? ??????

            # TODO: call ?????? ?????? taint ???????????? ??????, sub(b) ??????
            elif track_var.operation == MediumLevelILOperation.MLIL_CALL_SSA:
                pass
                # TODO: return??? ????????? taint ???????????? ??????

                taint.append(def_ref)

        return (stack_vars, global_vars, heap_vars)


    # def get_simple_path(self, source: target, sink: target) -> list[callHierarchy]:
    #     '''source ?????? sink ?????? ?????? ??? ?????? ?????? ?????????????????? path ??????'''
    #     source_group = self.backward_analysis_from_target(source)
    #     # print(source_group)
    #     result = []

    #     # when there are source and sink in same function.
    #     if source.function == sink.function:
    #         subgraph = self.graph.subgraph([source.function]).copy()
    #         result.append(callHierarchy(head=source.function, source=source, sink=sink, graph=subgraph))
    #         return result

    #     for head in source_group:
    #         if head == source.function:
    #             paths_to_source = [source.function]
    #         else:
    #             paths_to_source = list(nx.all_simple_paths(self.graph, head, source.function))
            
    #         #print('path_to_source', paths_to_source)
    #         if head == sink.function:
    #             paths_to_sink = [sink.function]
    #         else:
    #             paths_to_sink = list(nx.all_simple_paths(self.graph, head, sink.function))

    #         if len(paths_to_sink) > 0:
    #             #print('head', head)
    #             # some data structure with head, source, sink ..? or subgraph
    #             for path_to_sink in paths_to_sink:
    #                 #print('path_to_sink', path_to_sink)
    #                 for path_to_source in paths_to_source:
    #                     #print('path_to_source', path_to_source)
    #                     subgraph = self.graph.subgraph(list(path_to_sink) + list(path_to_source)).copy()
    #                     result.append(callHierarchy(head=head, source=source, sink=sink, graph=subgraph))
        
    #     # update edge with call_sites attribute
    #     for callgraph in result:
    #         callgraph: callHierarchy
    #         edges = nx.bfs_edges(callgraph.graph, callgraph.head)
    #         for start, end in edges:
    #             #print('start', start, end)
    #             call_sites: list[ReferenceSource] = []
    #             for call_site in start.call_sites: # start ????????? end??? ????????? ??????
    #                 call_site: ReferenceSource
    #                 mlil = call_site.function.get_llil_at(call_site.address).mlil.ssa_form
    #                 print('call_site', call_site)
    #                 if mlil.operation == MediumLevelILOperation.MLIL_CALL:
    #                     try:
    #                         if mlil.dest.constant == end.start:
    #                             call_sites.append(call_site)
    #                     except:
    #                         print('indirect call!')
    #             nx.set_edge_attributes(callgraph.graph, {(start, end): {'call_sites': call_sites}})
    #             print(start, end, call_sites)

    #     return result

    def show_pathobject(self):
        result = f'''\nName: {self.name}.html\nThis function's type is {self.type} with {self.option} option
        '''
        result += f'{RED}Full Path : '
        if self.type == PathType.SINGLE_FUNCTION:
            result += self.head.function.name if self.head.function.name is not None else f'{self.head.function.start:#x}'
            result += f'{END}\n'
            result += f'{GREEN}{self.head}{END}\n'
            result += f'{YELLOW}SOURCE:{self.source}{END}\n'
            result += f'{YELLOW}SINK:{self.sink}{END}\n'

        elif self.type == PathType.LINEAR_NODES:
            result += self.head.function.name if self.head.function.name is not None else f'{self.head.function.start:#x}'
            for _, end, _ in self.path:
                result += f' -> ' + end.name if end.name is not None else f'{end.start:#x}'
            result += f'{END}\n'
            
            for start, end, call_site in self.path:
                result += f'{GREEN}{self.nodes.get(start)}{END}'
                result += f'{YELLOW}{self.edges.get(call_site)}{END}'
            result += f'{GREEN}{self.nodes.get(self.sink.start)}{END}'
            result += f'{YELLOW}{self.sink}{END}'

        elif self.type == PathType.TREE_NODES:
            result += self.head.function.name if self.head.function.name is not None else f'{self.head.function.start:#x}'
            result += f'{END}\n'
            result += f'SORRY NOT IMPLEMENTED YET!'
            pass

        result += '\n'
        print(result)
        

    def save_graph(self, filename:str = None):
        from pyvis.network import Network

        a = nx.DiGraph()

        for start, end in self.graph.edges:
            start: PNode
            end: PNode
            
            name1 = start.function.name if start.function.name is not None else f'{start.function.start:#x}'
            name2 = end.function.name if end.function.name is not None else f'{end.function.start:#x}'
            a.add_edge(name1, name2)

        net = Network(directed=True)
        net.from_nx(a)
        if filename is None:
            net.show(self.name + '.html')
        else:
            net.show(f'{filename}.html')
    
    def save_bndb_file_by_path(self, filename: str = None):
        name = filename
        if filename is None:
            name = self.name
        
        for func, instr_addrs in self.highlight_addr.items():
            for instr_addr in instr_addrs:
                func.set_user_instr_highlight(addr=instr_addr, color=HighlightStandardColor.BlueHighlightColor)
        
        settings = SaveSettings()
        self.bv.file.create_database(f"{name}.bndb", None, settings)