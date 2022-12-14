from utils.utils import *
from utils.path.path_finder import *
from binaryninja import *
from utils.angr_manager import AngrManager
import logging

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)


if __name__ == '__main__':

    binary = 'D:\\Projects\\binary-nomaj\\C\\testcases\\CWE134_Uncontrolled_Format_String\\s01\\CWE134_Uncontrolled_Format_String__char_connect_socket_printf_45.out'
    binary = 'D:\\Projects\\binary-nomaj\\C\\testcases\\CWE190_Integer_Overflow\\s01\\CWE190_Integer_Overflow__char_fscanf_multiply_67.out'
    
    bv = binaryview.BinaryViewType.get_view_of_file(binary)
    
    #from CWE134_Uncontrolled_Format_String.format_string import source_targets, sink_targets, solution
    from CWE190_Integer_Overflow.integer_overflow import source_targets, solution

    sources = make_targets(bv, source_targets)
    sinks = make_arithmetic_targets(bv)

    pf = PathFinder(bv=bv, sources=sources, sinks=sinks, option=PathGenOption.POSSIBLE_VALUE_UPDATE)
    paths = pf.generate_path()
    #pf.save_entire_graph('test_entire_graph.html')
    for path in paths:
        path.show_pathobject() # for debug, you can view all element of node and edge
        #path.save_graph() # if name is None, filename is random
        #path.save_bndb_file_by_path()

        angr_manager = AngrManager(path=path)
        if angr_manager.check_feasible():
            print(f'This Path are feasible!')
            solution(bv, path)
